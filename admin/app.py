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
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    session,
    send_file,
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
    TELEGRAM_BOT_USERNAME,
    BUSINESS_PHONE,
    BUSINESS_ADDRESS,
    BUSINESS_WEBSITE,
    BOT_TONE_PRESETS,
)
from ai_chatbot.rag.engine import rebuild_index, mark_index_stale, is_index_stale
from ai_chatbot.live_chat_service import LiveChatService, send_telegram_message
from ai_chatbot.referral_service import try_send_referral_code
from ai_chatbot.vacation_service import VacationService
from ai_chatbot.business_hours import DAY_NAMES_HE

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
    """Format a UTC datetime string to Israel time as DD-MM-YYYY  HH:MM.

    משתמש ב-non-breaking space (\\u00a0) כדי שהדפדפן לא יקרוס את הרווח בין
    התאריך לשעה (whitespace collapse).
    """
    if not value:
        return ""
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc).astimezone(ISRAEL_TZ)
        return dt.strftime("%d-%m-%Y") + "\u00a0\u00a0" + dt.strftime("%H:%M")
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
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

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
                if request.form.get("remember_me"):
                    session.permanent = True
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
        referral_stats = db.get_referral_stats()
        stats = {
            "kb_entries": db.count_kb_entries(active_only=True),
            "categories": db.count_kb_categories(active_only=True),
            "users": db.count_unique_users(),
            "pending_requests": db.count_agent_requests(status="pending"),
            "pending_appointments": db.count_appointments(status="pending"),
            "active_live_chats": LiveChatService.count_active(),
            "open_knowledge_gaps": db.count_unanswered_questions(status="open"),
            "completed_referrals": referral_stats["completed_referrals"],
        }

        pending_requests = db.get_agent_requests(status="pending", limit=5)
        pending_appointments = db.get_appointments(status="pending", limit=5)
        active_live_chats = LiveChatService.get_all_active()
        recent_gaps = db.get_unanswered_questions(status="open", limit=5)

        return render_template(
            "dashboard.html",
            business_name=BUSINESS_NAME,
            stats=stats,
            recent_requests=pending_requests,
            recent_appointments=pending_appointments,
            active_live_chats=active_live_chats,
            recent_gaps=recent_gaps,
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
            gap_id = request.form.get("gap_id", "").strip()

            if not all([category, title, content]):
                flash("כל השדות הם חובה.", "danger")
            else:
                db.add_kb_entry(category, title, content)
                mark_index_stale()
                # Auto-resolve the knowledge gap if this entry was added from one
                if gap_id:
                    try:
                        db.update_unanswered_question_status(int(gap_id), "resolved")
                    except (ValueError, Exception):
                        pass
                flash(f"הרשומה '{title}' נוספה בהצלחה!", "success")
                return redirect(url_for("kb_list"))

        # Pre-fill from knowledge gap link
        prefill_question = request.args.get("question", "")
        gap_id = request.args.get("gap_id", "")

        categories = db.get_kb_categories()
        return render_template(
            "kb_form.html",
            business_name=BUSINESS_NAME,
            entry=None,
            categories=categories,
            action="Add",
            prefill_question=prefill_question,
            gap_id=gap_id,
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
        active_live_chats = {lc["user_id"] for lc in LiveChatService.get_all_active()}
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

    def require_active_live_chat(f):
        """Admin decorator: reject request if the live chat session is not active."""
        @wraps(f)
        def decorated(user_id, *args, **kwargs):
            if not LiveChatService.is_active(user_id):
                if request.headers.get("HX-Request"):
                    resp = app.make_response(("", 409))
                    resp.headers["HX-Trigger"] = json.dumps(
                        {"showToast": {"message": "השיחה החיה הסתיימה. רעננו את הדף.", "type": "warning"}}
                    )
                    return resp
                flash("השיחה החיה הסתיימה.", "warning")
                return redirect(url_for("live_chat", user_id=user_id))
            return f(user_id, *args, **kwargs)
        return decorated

    @app.route("/live-chat/<user_id>")
    @login_required
    def live_chat(user_id):
        live_session = LiveChatService.get_session(user_id)
        messages = db.get_conversation_history(user_id, limit=100)
        username = LiveChatService.get_customer_username(user_id)
        return render_template(
            "live_chat.html",
            business_name=BUSINESS_NAME,
            user_id=user_id,
            username=username,
            messages=messages,
            live_session=live_session,
        )

    @app.route("/live-chat/<user_id>/start", methods=["POST"])
    @login_required
    def live_chat_start(user_id):
        sent, status = LiveChatService.start(user_id)
        if status == "already_active":
            flash("השיחה החיה כבר פעילה.", "info")
        elif status == "telegram_failed":
            flash("השיחה החיה הופעלה, אך ההודעה ללקוח בטלגרם נכשלה.", "warning")
        return redirect(url_for("live_chat", user_id=user_id))

    @app.route("/live-chat/<user_id>/end", methods=["POST"])
    @login_required
    def live_chat_end(user_id):
        back = _safe_redirect_back(url_for("conversations"))
        sent, status = LiveChatService.end(user_id)
        if status == "already_ended":
            flash("השיחה החיה כבר הסתיימה.", "info")
        elif status == "telegram_failed":
            flash("השיחה הוחזרה לבוט, אך ההודעה ללקוח בטלגרם נכשלה.", "warning")
        return redirect(back)

    @app.route("/live-chat/<user_id>/send", methods=["POST"])
    @login_required
    @require_active_live_chat
    def live_chat_send(user_id):
        message_text = request.form.get("message", "").strip()
        success, status = LiveChatService.send(user_id, message_text)

        if not success:
            error_messages = {
                "session_ended": ("השיחה החיה הסתיימה.", "warning", 409),
                "empty_message": ("לא ניתן לשלוח הודעה ריקה.", "danger", 422),
                "telegram_failed": ("שליחת ההודעה בטלגרם נכשלה.", "danger", 500),
            }
            msg, level, code = error_messages.get(status, ("שגיאה לא צפויה.", "danger", 500))
            if request.headers.get("HX-Request"):
                resp = app.make_response(("", code))
                if status != "empty_message":
                    resp.headers["HX-Trigger"] = json.dumps(
                        {"showToast": {"message": msg, "type": level}}
                    )
                return resp
            flash(msg, level)
            return redirect(url_for("live_chat", user_id=user_id))

        if request.headers.get("HX-Request"):
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

        # הפעלת מערכת הפניות — כשתור מאושר, בודקים אם הלקוח הגיע דרך הפניה
        if status == "confirmed":
            appt = db.get_appointment(appt_id)
            if appt:
                user_id = appt["user_id"]
                if db.has_pending_referral(user_id):
                    activated = db.complete_referral(user_id)
                    if activated:
                        logger.info(
                            "Referral completed for user %s (appointment #%d)",
                            user_id, appt_id,
                        )

                # שליחת קוד הפניה ללקוח אחרי אישור תור
                # try_send_referral_code — לוגיקה משותפת לבוט ולאדמין:
                # generate → mark → send → unmark on failure
                try_send_referral_code(
                    user_id,
                    send_fn=lambda text: send_telegram_message(user_id, text),
                )

        if request.headers.get("HX-Request"):
            appt = db.get_appointment(appt_id)
            if appt:
                return render_template("partials/appointment_row.html", appt=appt)
            return ""
        flash(f"תור #{appt_id} סומן כ-{status}.", "success")
        return redirect(url_for("appointments"))
    
    # ─── Knowledge Gaps (Unanswered Questions) ─────────────────────────────

    VALID_UNANSWERED_STATUSES = {"open", "resolved"}

    @app.route("/knowledge-gaps")
    @login_required
    def knowledge_gaps():
        status_filter = request.args.get("status", None)
        questions = db.get_unanswered_questions(status=status_filter)
        open_count = db.count_unanswered_questions(status="open")
        return render_template(
            "knowledge_gaps.html",
            business_name=BUSINESS_NAME,
            questions=questions,
            current_status=status_filter,
            open_count=open_count,
        )

    @app.route("/knowledge-gaps/<int:question_id>/resolve", methods=["POST"])
    @login_required
    def resolve_question(question_id):
        status = request.form.get("status", "resolved")
        if status not in VALID_UNANSWERED_STATUSES:
            if request.headers.get("HX-Request"):
                resp = app.make_response(("", 422))
                resp.headers["HX-Trigger"] = json.dumps(
                    {"showToast": {"message": "סטטוס לא חוקי.", "type": "danger"}}
                )
                return resp
            flash("סטטוס לא חוקי.", "danger")
            return redirect(url_for("knowledge_gaps"))
        db.update_unanswered_question_status(question_id, status)

        if request.headers.get("HX-Request"):
            q = db.get_unanswered_question(question_id)
            if q:
                return render_template("partials/knowledge_gap_row.html", q=q)
            return ""
        flash(f"שאלה #{question_id} עודכנה.", "success")
        return redirect(url_for("knowledge_gaps"))

    # ─── Business Hours ──────────────────────────────────────────────────

    @app.route("/business-hours")
    @login_required
    def business_hours():
        hours = db.get_all_business_hours()
        special_days = db.get_all_special_days()
        return render_template(
            "business_hours.html",
            business_name=BUSINESS_NAME,
            hours=hours,
            special_days=special_days,
            day_names=DAY_NAMES_HE,
        )

    @app.route("/business-hours/update", methods=["POST"])
    @login_required
    def business_hours_update():
        for day in range(7):
            is_closed = request.form.get(f"closed_{day}") == "on"
            open_time = request.form.get(f"open_{day}", "").strip()
            close_time = request.form.get(f"close_{day}", "").strip()
            db.upsert_business_hours(day, open_time, close_time, is_closed)
        flash("שעות הפעילות עודכנו בהצלחה!", "success")
        return redirect(url_for("business_hours"))

    @app.route("/business-hours/special-days/add", methods=["POST"])
    @login_required
    def special_day_add():
        date_str = request.form.get("date", "").strip()
        name = request.form.get("name", "").strip()
        is_closed = request.form.get("is_closed") == "on"
        open_time = request.form.get("open_time", "").strip() or None
        close_time = request.form.get("close_time", "").strip() or None
        notes = request.form.get("notes", "").strip()

        if not date_str or not name:
            flash("תאריך ושם הם שדות חובה.", "danger")
            return redirect(url_for("business_hours"))

        db.add_special_day(date_str, name, is_closed, open_time, close_time, notes)
        flash(f"יום מיוחד '{name}' נוסף בהצלחה!", "success")
        return redirect(url_for("business_hours"))

    @app.route("/business-hours/special-days/<int:sd_id>/edit", methods=["POST"])
    @login_required
    def special_day_edit(sd_id):
        date_str = request.form.get("date", "").strip()
        name = request.form.get("name", "").strip()
        is_closed = request.form.get("is_closed") == "on"
        open_time = request.form.get("open_time", "").strip() or None
        close_time = request.form.get("close_time", "").strip() or None
        notes = request.form.get("notes", "").strip()

        if not date_str or not name:
            flash("תאריך ושם הם שדות חובה.", "danger")
            return redirect(url_for("business_hours"))

        db.update_special_day(sd_id, date_str, name, is_closed, open_time, close_time, notes)
        flash(f"יום מיוחד '{name}' עודכן בהצלחה!", "success")
        return redirect(url_for("business_hours"))

    @app.route("/business-hours/special-days/<int:sd_id>/delete", methods=["POST"])
    @login_required
    def special_day_delete(sd_id):
        db.delete_special_day(sd_id)
        if request.headers.get("HX-Request"):
            return ""
        flash("יום מיוחד נמחק.", "success")
        return redirect(url_for("business_hours"))

    # ─── Vacation Mode ─────────────────────────────────────────────────────

    @app.route("/vacation-mode", methods=["GET", "POST"])
    @login_required
    def vacation_mode():
        if request.method == "POST":
            is_active = request.form.get("is_active") == "on"
            vacation_end_date = request.form.get("vacation_end_date", "").strip()
            vacation_message = request.form.get("vacation_message", "").strip()
            db.update_vacation_mode(is_active, vacation_end_date, vacation_message)
            if is_active:
                flash("מצב חופשה הופעל!", "success")
            else:
                flash("מצב חופשה כובה.", "info")
            return redirect(url_for("vacation_mode"))

        vacation = db.get_vacation_mode()
        # תצוגה מקדימה — משתמש ב-VacationService כדי שהטקסט תמיד יתאים למה שהלקוח רואה
        preview_booking = VacationService.get_booking_message()
        preview_agent = VacationService.get_agent_message()
        return render_template(
            "vacation_mode.html",
            business_name=BUSINESS_NAME,
            vacation=vacation,
            preview_booking=preview_booking,
            preview_agent=preview_agent,
        )

    # ─── Bot Personality (אישיות הבוט) ──────────────────────────────────

    @app.route("/bot-personality", methods=["GET", "POST"])
    @login_required
    def bot_personality():
        if request.method == "POST":
            tone = request.form.get("tone", "friendly").strip()
            # וידוא שהטון קיים ברשימה
            if tone not in BOT_TONE_PRESETS:
                tone = "friendly"
            custom_instructions = request.form.get("custom_instructions", "").strip()
            db.update_bot_personality(tone, custom_instructions)
            flash("אישיות הבוט עודכנה!", "success")
            return redirect(url_for("bot_personality"))

        personality = db.get_bot_personality()
        tone_key = personality.get("tone", "friendly")
        label, instruction = BOT_TONE_PRESETS.get(tone_key, BOT_TONE_PRESETS["friendly"])
        return render_template(
            "bot_personality.html",
            business_name=BUSINESS_NAME,
            personality=personality,
            tone_presets=BOT_TONE_PRESETS,
            current_tone_label=label,
            current_tone_instruction=instruction,
        )

    # ─── Referrals (מערכת הפניות) ────────────────────────────────────────

    @app.route("/referrals")
    @login_required
    def referrals():
        stats = db.get_referral_stats()
        top_referrers = db.get_top_referrers(limit=10)
        all_referrals = db.get_all_referrals(limit=50)

        # הוספת שמות תצוגה למפנים מובילים
        for ref in top_referrers:
            name = db.get_username_for_user(ref["referrer_id"])
            ref["display_name"] = name or ref["referrer_id"]

        return render_template(
            "referrals.html",
            business_name=BUSINESS_NAME,
            stats=stats,
            top_referrers=top_referrers,
            all_referrals=all_referrals,
        )

    # ─── QR Code ──────────────────────────────────────────────────────────

    @app.route("/qr-code")
    @login_required
    def qr_code():
        return render_template(
            "qr_code.html",
            business_name=BUSINESS_NAME,
            bot_username=TELEGRAM_BOT_USERNAME,
        )

    @app.route("/qr-code/download")
    @login_required
    def qr_code_download():
        """יצירת QR Code כקובץ PNG להורדה."""
        if not TELEGRAM_BOT_USERNAME:
            flash("לא הוגדר TELEGRAM_BOT_USERNAME. יש להגדיר ב-.env.", "danger")
            return redirect(url_for("qr_code"))

        import segno

        bot_url = f"https://t.me/{TELEGRAM_BOT_USERNAME}"
        # קריאת פרמטרי עיצוב מה-query string
        dark_color = request.args.get("color", "#000000")
        scale = int(request.args.get("scale", "10"))
        # הגבלת scale לטווח סביר
        scale = max(1, min(scale, 50))

        qr = segno.make(bot_url, error="H")
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=scale, dark=dark_color, light="#FFFFFF", border=2)
        buf.seek(0)

        filename = f"qr_{TELEGRAM_BOT_USERNAME}.png"
        return send_file(buf, mimetype="image/png", as_attachment=True, download_name=filename)

    @app.route("/qr-code/preview")
    @login_required
    def qr_code_preview():
        """יצירת תמונת QR Code לתצוגה מקדימה (inline)."""
        if not TELEGRAM_BOT_USERNAME:
            return "", 404

        import segno

        bot_url = f"https://t.me/{TELEGRAM_BOT_USERNAME}"
        dark_color = request.args.get("color", "#000000")

        qr = segno.make(bot_url, error="H")
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=10, dark=dark_color, light="#FFFFFF", border=2)
        buf.seek(0)

        return send_file(buf, mimetype="image/png")

    # ─── API Endpoints (for AJAX) ─────────────────────────────────────────

    @app.route("/api/stats")
    @login_required
    def api_stats():
        vacation = db.get_vacation_mode()
        return jsonify({
            "pending_requests": db.count_agent_requests(status="pending"),
            "pending_appointments": db.count_appointments(status="pending"),
            "active_live_chats": LiveChatService.count_active(),
            "open_knowledge_gaps": db.count_unanswered_questions(status="open"),
            "vacation_active": bool(vacation["is_active"]),
        })

    return app


def run_admin():
    """Start the Flask admin panel (blocking call)."""
    logger.info("Starting admin panel on %s:%s", ADMIN_HOST, ADMIN_PORT)
    app = create_admin_app()
    app.run(host=ADMIN_HOST, port=ADMIN_PORT, debug=False)
