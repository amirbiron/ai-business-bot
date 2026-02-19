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
import logging
from functools import wraps
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
)
from ai_chatbot.rag.engine import rebuild_index

logger = logging.getLogger(__name__)

VALID_AGENT_REQUEST_STATUSES = {"pending", "handled", "dismissed"}
VALID_APPOINTMENT_STATUSES = {"pending", "confirmed", "cancelled"}


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
    if not hmac.compare_digest(str(username), str(ADMIN_USERNAME)):
        return False
    if ADMIN_PASSWORD_HASH:
        try:
            return check_password_hash(ADMIN_PASSWORD_HASH, password)
        except Exception:
            return False
    return hmac.compare_digest(str(password), str(ADMIN_PASSWORD))


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

    @app.errorhandler(CSRFError)
    def _handle_csrf_error(e):
        # Avoid leaking CSRF details; just ask the user to retry.
        flash("פג תוקף הטופס. נסו שוב.", "danger")
        return redirect(request.referrer or url_for("login"))
    
    # ─── Auth Decorator ───────────────────────────────────────────────────
    
    def login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("logged_in"):
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
        kb_entries = db.get_all_kb_entries()
        categories = db.get_kb_categories()
        users = db.get_unique_users()
        pending_requests = db.get_agent_requests(status="pending")
        pending_appointments = db.get_appointments(status="pending")
        
        stats = {
            "kb_entries": len(kb_entries),
            "categories": len(categories),
            "users": len(users),
            "pending_requests": len(pending_requests),
            "pending_appointments": len(pending_appointments),
        }
        
        return render_template(
            "dashboard.html",
            business_name=BUSINESS_NAME,
            stats=stats,
            recent_requests=pending_requests[:5],
            recent_appointments=pending_appointments[:5],
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
        flash("הרשומה נמחקה.", "success")
        return redirect(url_for("kb_list"))
    
    @app.route("/kb/rebuild", methods=["POST"])
    @login_required
    def kb_rebuild():
        try:
            rebuild_index()
            flash("אינדקס RAG נבנה מחדש בהצלחה!", "success")
        except Exception as e:
            logger.error(f"Index rebuild failed: {e}")
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
        
        return render_template(
            "conversations.html",
            business_name=BUSINESS_NAME,
            users=users,
            messages=messages,
            selected_user=selected_user,
        )
    
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
            flash("סטטוס לא חוקי.", "danger")
            return redirect(url_for("agent_requests"))
        db.update_agent_request_status(request_id, status)
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
            flash("סטטוס לא חוקי.", "danger")
            return redirect(url_for("appointments"))
        db.update_appointment_status(appt_id, status)
        flash(f"תור #{appt_id} סומן כ-{status}.", "success")
        return redirect(url_for("appointments"))
    
    # ─── API Endpoints (for AJAX) ─────────────────────────────────────────
    
    @app.route("/api/stats")
    @login_required
    def api_stats():
        pending_requests = db.get_agent_requests(status="pending")
        pending_appointments = db.get_appointments(status="pending")
        return jsonify({
            "pending_requests": len(pending_requests),
            "pending_appointments": len(pending_appointments),
        })
    
    return app


def run_admin():
    """Start the Flask admin panel (blocking call)."""
    logger.info(f"Starting admin panel on {ADMIN_HOST}:{ADMIN_PORT}")
    app = create_admin_app()
    app.run(host=ADMIN_HOST, port=ADMIN_PORT, debug=False)
