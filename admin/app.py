"""
Web Admin Panel — Flask application for business owners to manage the chatbot.

Features:
- Dashboard with stats
- Knowledge Base management (CRUD)
- Conversation logs viewer
- Agent request notifications
- Appointment management
- Rebuild RAG index
"""

import hmac
import json
import logging
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests as http_requests

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    session,
)

from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.security import check_password_hash

from ai_chatbot import database as db
from ai_chatbot.config import (
    ADMIN_USERNAME,
    ADMIN_PASSWORD,
    ADMIN_PASSWORD_HASH,
    ADMIN_SECRET_KEY,
    ADMIN_HOST,
    ADMIN_PORT,
    BUSINESS_NAME,
    TELEGRAM_BOT_TOKEN,
)
from ai_chatbot.rag.engine import rebuild_index, mark_index_stale, is_index_stale

logger = logging.getLogger(__name__)

VALID_AGENT_REQUEST_STATUSES = {"pending", "handled", "dismissed"}
VALID_APPOINTMENT_STATUSES = {"pending", "confirmed", "cancelled"}

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

CATEGORY_TRANSLATION = {
    "Staff": "הצוות",
    "Services": "שירותים",
    "Promotions": "הטבות",
    "Pricing": "מחירון",
    "Policies": "מדיניות",
    "Location": "מיקום",
    "Hours": "שעות",
    "FAQ": "שאלות נפוצות",
}

STATUS_TRANSLATION = {
    "pending": "ממתין",
    "handled": "טופל",
    "dismissed": "נדחה",
    "confirmed": "מאושר",
    "cancelled": "בוטל",
}


def _format_il_datetime(value: str) -> str:
    """Format a UTC datetime string to Israel time as DD-MM-YYYY HH:MM."""
    if not value:
        return ""
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc).astimezone(ISRAEL_TZ)
        return dt.strftime("%d-%m-%Y %H:%M")
    except (ValueError, TypeError):
        return value


def _translate_category(value: str) -> str:
    """Translate an English KB category name to Hebrew."""
    return CATEGORY_TRANSLATION.get(value, value)


def _translate_status(value: str) -> str:
    """Translate an English status to Hebrew."""
    return STATUS_TRANSLATION.get(value, value)


def _validate_admin_security_config() -> None:
    if not ADMIN_SECRET_KEY:
        raise RuntimeError(
            "ADMIN_SECRET_KEY must be set (required for session + CSRF protection)."
        )
    if not ADMIN_USERNAME:
        raise RuntimeError("ADMIN_USERNAME must be set.")
    if not (ADMIN_PASSWORD_HASH or ADMIN_PASSWORD):
        raise RuntimeError(
            "Either ADMIN_PASSWORD_HASH (recommended) or ADMIN_PASSWORD must be set."
        )


def _verify_admin_credentials(username: str, password: str) -> bool:
    if not username or not password:
        return False

    username_ok = hmac.compare_digest(str(username), str(ADMIN_USERNAME))

    # Always perform the password check to avoid a timing oracle that can
    # distinguish "wrong username" from "right username, wrong password".
    if ADMIN_PASSWORD_HASH:
        try:
            password_ok = check_password_hash(ADMIN_PASSWORD_HASH, str(password))
        except Exception:
            password_ok = False
    else:
        password_ok = hmac.compare_digest(str(password), str(ADMIN_PASSWORD))

    return username_ok and password_ok


def _safe_redirect_back(default_url: str) -> str:
    """
    Return a safe same-origin redirect target derived from Referer, or a default.
    """
    ref = request.referrer
    if not ref:
        return default_url
    try:
        ref_url = urlparse(ref)
        host_url = urlparse(request.host_url)
        if ref_url.scheme in ("http", "https") and ref_url.netloc == host_url.netloc:
            path = ref_url.path or "/"
            # Prevent protocol-relative redirects (e.g. "//evil.com") and require an absolute path.
            if not path.startswith("/") or path.startswith("//"):
                return default_url
            return f"{path}?{ref_url.query}" if ref_url.query else path
    except Exception:
        return default_url
    return default_url


def create_admin_app() -> Flask:
    """Create and configure the Flask admin application."""
    _validate_admin_security_config()
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.secret_key = ADMIN_SECRET_KEY

    csrf = CSRFProtect()
    csrf.init_app(app)

    app.jinja_env.filters["il_datetime"] = _format_il_datetime
    app.jinja_env.filters["translate_category"] = _translate_category
    app.jinja_env.filters["translate_status"] = _translate_status

    @app.context_processor
    def _inject_rag_index_state():
        return {"rag_index_stale": is_index_stale()}

    @app.errorhandler(CSRFError)
    def _handle_csrf_error(e):
        if request.headers.get("HX-Request"):
            # Return a lightweight 403 so HTMX doesn't replace content with
            # a full redirect page.  The csrfExpired trigger tells client JS
            # to show a reload prompt.
            resp = app.make_response(("", 403))
            # Prevent any DOM swap on HTMX requests.
            resp.headers["HX-Reswap"] = "none"
            resp.headers["HX-Trigger"] = "csrfExpired"
            return resp
        # Regular form submission — flash and redirect.
        flash("פג תוקף הטופס. נסו שוב.", "danger")
        default = url_for("dashboard") if session.get("logged_in") else url_for("login")
        return redirect(_safe_redirect_back(default))
    
    # ─── Auth Decorator ───────────────────────────────────────────────────
    
    def login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("logged_in"):
                if request.headers.get("HX-Request"):
                    resp = app.make_response(("", 401))
                    resp.headers["HX-Redirect"] = url_for("login")
                    return resp
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated
    
    # ─── Auth Routes ──────────────────────────────────────────────────────
    
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if _verify_admin_credentials(username, password):
                session["logged_in"] = True
                flash("ברוכים השבים!", "success")
                return redirect(url_for("dashboard"))
            flash("פרטי התחברות שגויים.", "danger")
        return render_template("login.html", business_name=BUSINESS_NAME)
    
    @app.route("/logout")
    def logout():
        session.clear()
        flash("התנתקת בהצלחה.", "info")
        return redirect(url_for("login"))
    
    # ─── Dashboard ────────────────────────────────────────────────────────
    
    @app.route("/")
    @login_required
    def dashboard():
        stats = {
            "kb_entries": db.count_kb_entries(active_only=True),
            "categories": db.count_kb_categories(active_only=True),
            "users": db.count_unique_users(),
            "pending_requests": db.count_agent_requests(status="pending"),
            "pending_appointments": db.count_appointments(status="pending"),
            "active_live_chats": db.count_active_live_chats(),
        }

        pending_requests = db.get_agent_requests(status="pending", limit=5)
        pending_appointments = db.get_appointments(status="pending", limit=5)
        active_live_chats = db.get_all_active_live_chats()

        return render_template(
            "dashboard.html",
            business_name=BUSINESS_NAME,
            stats=stats,
            recent_requests=pending_requests,
            recent_appointments=pending_appointments,
            active_live_chats=active_live_chats,
        )
    
    # ─── Knowledge Base Management ────────────────────────────────────────
    
    @app.route("/kb")
    @login_required
    def kb_list():
        category_filter = request.args.get("category", None)
        entries = db.get_all_kb_entries(category=category_filter, active_only=False)
        categories = db.get_kb_categories()
        return render_template(
            "kb_list.html",
            business_name=BUSINESS_NAME,
            entries=entries,
            categories=categories,
            current_category=category_filter,
        )
    
    @app.route("/kb/add", methods=["GET", "POST"])
    @login_required
    def kb_add():
        if request.method == "POST":
            category = request.form.get("category", "").strip()
            title = request.form.get("title", "").strip()
            content = request.form.get("content", "").strip()
            
            if not all([category, title, content]):
                flash("כל השדות הם חובה.", "danger")
            else:
                db.add_kb_entry(category, title, content)
                mark_index_stale()
                flash(f"הרשומה '{title}' נוספה בהצלחה!", "success")
                return redirect(url_for("kb_list"))
        
        categories = db.get_kb_categories()
        return render_template(
            "kb_form.html",
            business_name=BUSINESS_NAME,
            entry=None,
            categories=categories,
            action="Add",
        )
    
    @app.route("/kb/edit/<int:entry_id>", methods=["GET", "POST"])
    @login_required
    def kb_edit(entry_id):
        entry = db.get_kb_entry(entry_id)
        if not entry:
            flash("הרשומה לא נמצאה.", "danger")
            return redirect(url_for("kb_list"))
        
        if request.method == "POST":
            category = request.form.get("category", "").strip()
            title = request.form.get("title", "").strip()
            content = request.form.get("content", "").strip()
            
            if not all([category, title, content]):
                flash("כל השדות הם חובה.", "danger")
            else:
                db.update_kb_entry(entry_id, category, title, content)
                mark_index_stale()
                flash(f"הרשומה '{title}' עודכנה בהצלחה!", "success")
                return redirect(url_for("kb_list"))
        
        categories = db.get_kb_categories()
        return render_template(
            "kb_form.html",
            business_name=BUSINESS_NAME,
            entry=entry,
            categories=categories,
            action="Edit",
        )
    
    @app.route("/kb/delete/<int:entry_id>", methods=["POST"])
    @login_required
    def kb_delete(entry_id):
        db.delete_kb_entry(entry_id)
        mark_index_stale()
        if request.headers.get("HX-Request"):
            if db.count_kb_entries(active_only=False) == 0:
                resp = app.make_response(
                    render_template("partials/kb_empty.html")
                )
                resp.headers["HX-Retarget"] = "#kb-table-wrapper"
                resp.headers["HX-Reswap"] = "outerHTML"
            else:
                resp = app.make_response("")
            resp.headers["HX-Trigger"] = "showStaleWarning"
            return resp
        flash("הרשומה נמחקה.", "success")
        return redirect(url_for("kb_list"))
    
    @app.route("/kb/rebuild", methods=["POST"])
    @login_required
    def kb_rebuild():
        try:
            rebuild_index()
            flash("אינדקס RAG נבנה מחדש בהצלחה!", "success")
        except Exception as e:
            logger.error("Index rebuild failed: %s", e)
            flash(f"בניית האינדקס נכשלה: {str(e)}", "danger")
        return redirect(url_for("kb_list"))
    
    # ─── Conversations ────────────────────────────────────────────────────
    
    @app.route("/conversations")
    @login_required
    def conversations():
        users = db.get_unique_users()
        selected_user = request.args.get("user_id", None)

        if selected_user:
            messages = db.get_conversation_history(selected_user, limit=100)
        else:
            messages = db.get_all_conversations(limit=200)

        # Build a set of user_ids with active live chats for quick lookup
        active_live_chats = {lc["user_id"] for lc in db.get_all_active_live_chats()}
        # Pending agent requests (transfer notifications)
        pending_requests = db.get_agent_requests(status="pending")

        return render_template(
            "conversations.html",
            business_name=BUSINESS_NAME,
            users=users,
            messages=messages,
            selected_user=selected_user,
            active_live_chats=active_live_chats,
            pending_requests=pending_requests,
        )
    
    # ─── Live Chat ────────────────────────────────────────────────────────

    def _send_telegram_message(chat_id: str, text: str) -> bool:
        """Send a message to a Telegram user via the Bot API."""
        if not TELEGRAM_BOT_TOKEN:
            return False
        try:
            resp = http_requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
            return resp.ok
        except Exception as e:
            logger.error("Failed to send Telegram message to %s: %s", chat_id, e)
            return False

    def _get_customer_username(user_id: str) -> str:
        """Look up the customer's display name for a given user_id."""
        return db.get_username_for_user(user_id) or user_id

    @app.route("/live-chat/<user_id>")
    @login_required
    def live_chat(user_id):
        live_session = db.get_active_live_chat(user_id)
        messages = db.get_conversation_history(user_id, limit=100)
        username = _get_customer_username(user_id)
        return render_template(
            "live_chat.html",
            business_name=BUSINESS_NAME,
            user_id=user_id,
            username=username,
            messages=messages,
            live_session=live_session,
        )

    def _do_start_live_chat(user_id: str) -> bool:
        """Shared logic: activate live chat, notify customer, save message.

        Returns True if the Telegram notification was sent successfully.
        """
        username = _get_customer_username(user_id)
        db.start_live_chat(user_id, username)
        notify_msg = "👤 נציג אנושי הצטרף לשיחה. כעת תקבלו מענה ישיר."
        sent = _send_telegram_message(user_id, notify_msg)
        if sent:
            db.save_message(user_id, username, "assistant", notify_msg)
        return sent

    @app.route("/live-chat/<user_id>/start", methods=["POST"])
    @login_required
    def live_chat_start(user_id):
        # Guard against duplicate starts (e.g. double-click, stale tab).
        if db.is_live_chat_active(user_id):
            flash("השיחה החיה כבר פעילה.", "info")
            return redirect(url_for("live_chat", user_id=user_id))
        sent = _do_start_live_chat(user_id)
        if not sent:
            flash("השיחה החיה הופעלה, אך ההודעה ללקוח בטלגרם נכשלה.", "warning")
        return redirect(url_for("live_chat", user_id=user_id))

    @app.route("/live-chat/<user_id>/end", methods=["POST"])
    @login_required
    def live_chat_end(user_id):
        # Redirect back to wherever the user came from (dashboard,
        # conversations, or the live-chat page itself).
        back = _safe_redirect_back(url_for("conversations"))
        # Guard against duplicate "end" clicks (e.g. stale page in another tab).
        if not db.is_live_chat_active(user_id):
            flash("השיחה החיה כבר הסתיימה.", "info")
            return redirect(back)
        username = _get_customer_username(user_id)
        # Notify the customer that the bot is back
        end_msg = "🤖 הבוט חזר לנהל את השיחה. אם תרצו לדבר עם נציג שוב, לחצו על 'דברו עם נציג'."
        sent = _send_telegram_message(user_id, end_msg)
        if sent:
            db.save_message(user_id, username, "assistant", end_msg)
        # Deactivate *after* sending the notification so the bot stays
        # suspended until the customer receives the transition message.
        db.end_live_chat(user_id)
        if not sent:
            flash("השיחה הוחזרה לבוט, אך ההודעה ללקוח בטלגרם נכשלה.", "warning")
        return redirect(back)

    @app.route("/live-chat/<user_id>/send", methods=["POST"])
    @login_required
    def live_chat_send(user_id):
        # Reject if the session was ended (e.g. from another tab) while
        # the form was still visible due to stale HTMX state.
        if not db.is_live_chat_active(user_id):
            if request.headers.get("HX-Request"):
                resp = app.make_response(("", 409))
                resp.headers["HX-Trigger"] = json.dumps(
                    {"showToast": {"message": "השיחה החיה הסתיימה. רעננו את הדף.", "type": "warning"}}
                )
                return resp
            flash("השיחה החיה הסתיימה.", "warning")
            return redirect(url_for("live_chat", user_id=user_id))

        message_text = request.form.get("message", "").strip()
        if not message_text:
            if request.headers.get("HX-Request"):
                return "", 422
            flash("לא ניתן לשלוח הודעה ריקה.", "danger")
            return redirect(url_for("live_chat", user_id=user_id))

        # Send via Telegram
        sent = _send_telegram_message(user_id, message_text)
        if not sent:
            if request.headers.get("HX-Request"):
                resp = app.make_response(("", 500))
                resp.headers["HX-Trigger"] = json.dumps(
                    {"showToast": {"message": "שליחת ההודעה בטלגרם נכשלה.", "type": "danger"}}
                )
                return resp
            flash("שליחת ההודעה בטלגרם נכשלה.", "danger")
            return redirect(url_for("live_chat", user_id=user_id))

        # Save in conversation history using the customer's display name
        # so get_unique_users() isn't corrupted by BUSINESS_NAME.
        username = _get_customer_username(user_id)
        db.save_message(user_id, username, "assistant", message_text)

        if request.headers.get("HX-Request"):
            # Return updated messages list
            messages = db.get_conversation_history(user_id, limit=100)
            return render_template("partials/live_chat_messages.html", messages=messages)

        return redirect(url_for("live_chat", user_id=user_id))

    @app.route("/api/live-chat/<user_id>/messages")
    @login_required
    def api_live_chat_messages(user_id):
        """Polling endpoint for live chat messages (HTMX)."""
        messages = db.get_conversation_history(user_id, limit=100)
        return render_template("partials/live_chat_messages.html", messages=messages)

    # ─── Agent Requests ───────────────────────────────────────────────────

    @app.route("/requests")
    @login_required
    def agent_requests():
        requests_list = db.get_agent_requests()
        return render_template(
            "requests.html",
            business_name=BUSINESS_NAME,
            requests=requests_list,
        )
    
    @app.route("/requests/<int:request_id>/handle", methods=["POST"])
    @login_required
    def handle_request(request_id):
        status = request.form.get("status", "handled")
        if status not in VALID_AGENT_REQUEST_STATUSES:
            if request.headers.get("HX-Request"):
                resp = app.make_response(("", 422))
                resp.headers["HX-Trigger"] = json.dumps(
                    {"showToast": {"message": "סטטוס לא חוקי.", "type": "danger"}}
                )
                return resp
            flash("סטטוס לא חוקי.", "danger")
            return redirect(url_for("agent_requests"))
        db.update_agent_request_status(request_id, status)

        if request.headers.get("HX-Request"):
            req = db.get_agent_request(request_id)
            if req:
                return render_template("partials/request_row.html", req=req)
            return ""
        flash(f"בקשה #{request_id} סומנה כ-{status}.", "success")
        return redirect(url_for("agent_requests"))
    
    # ─── Appointments ─────────────────────────────────────────────────────
    
    @app.route("/appointments")
    @login_required
    def appointments():
        appointments_list = db.get_appointments()
        return render_template(
            "appointments.html",
            business_name=BUSINESS_NAME,
            appointments=appointments_list,
        )
    
    @app.route("/appointments/<int:appt_id>/update", methods=["POST"])
    @login_required
    def update_appointment(appt_id):
        status = request.form.get("status", "confirmed")
        if status not in VALID_APPOINTMENT_STATUSES:
            if request.headers.get("HX-Request"):
                resp = app.make_response(("", 422))
                resp.headers["HX-Trigger"] = json.dumps(
                    {"showToast": {"message": "סטטוס לא חוקי.", "type": "danger"}}
                )
                return resp
            flash("סטטוס לא חוקי.", "danger")
            return redirect(url_for("appointments"))
        db.update_appointment_status(appt_id, status)
        if request.headers.get("HX-Request"):
            appt = db.get_appointment(appt_id)
            if appt:
                return render_template("partials/appointment_row.html", appt=appt)
            return ""
        flash(f"תור #{appt_id} סומן כ-{status}.", "success")
        return redirect(url_for("appointments"))
    
    # ─── API Endpoints (for AJAX) ─────────────────────────────────────────
    
    @app.route("/api/stats")
    @login_required
    def api_stats():
        return jsonify({
            "pending_requests": db.count_agent_requests(status="pending"),
            "pending_appointments": db.count_appointments(status="pending"),
            "active_live_chats": db.count_active_live_chats(),
        })
    
    return app


def run_admin():
    """Start the Flask admin panel (blocking call)."""
    logger.info("Starting admin panel on %s:%s", ADMIN_HOST, ADMIN_PORT)
    app = create_admin_app()
    app.run(host=ADMIN_HOST, port=ADMIN_PORT, debug=False)
