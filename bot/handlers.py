"""
Telegram Bot Handlers — all command and callback handlers for the customer-facing bot.

Features:
- /start — Welcome message with main menu buttons
- Free-text messages — Answered via RAG + LLM pipeline
- "Book Appointment" button — Starts appointment booking flow
- "Talk to Agent" button — Sends notification to business owner
- "Send Location" button — Sends business location
- "Price List" button — Shows the price list from KB
- Conversation history per user
"""

import asyncio
import html as _html
import logging
import time
from io import BytesIO
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.error import BadRequest, TimedOut, NetworkError
from telegram.ext import ContextTypes, ConversationHandler

from ai_chatbot import database as db
from ai_chatbot.llm import generate_answer, strip_source_citation, sanitize_telegram_html, maybe_summarize
from ai_chatbot.intent import Intent, detect_intent, get_direct_response
from ai_chatbot.business_hours import is_currently_open, get_weekly_schedule_text
from ai_chatbot.config import (
    BUSINESS_NAME,
    BUSINESS_PHONE,
    BUSINESS_ADDRESS,
    BUSINESS_WEBSITE,
    TELEGRAM_OWNER_CHAT_ID,
    FALLBACK_RESPONSE,
    CONTEXT_WINDOW_SIZE,
    FOLLOW_UP_ENABLED,
)
from ai_chatbot.entity_extraction import extract_dates
from ai_chatbot.live_chat_service import live_chat_guard, live_chat_guard_booking
from ai_chatbot.rate_limiter import rate_limit_guard, rate_limit_guard_booking, check_rate_limit, record_message
from ai_chatbot.vacation_service import (
    VacationService,
    vacation_guard_booking,
    vacation_guard_agent,
)

logger = logging.getLogger(__name__)

# Conversation states for appointment booking
BOOKING_SERVICE, BOOKING_DATE, BOOKING_TIME, BOOKING_CONFIRM = range(4)

# Button label constants — used for routing and filtering
BUTTON_PRICE_LIST = "📋 מחירון"
BUTTON_BOOKING = "📅 בקשת תור"
BUTTON_LOCATION = "📍 שליחת מיקום"
BUTTON_SAVE_CONTACT = "📇 שמור איש קשר"
BUTTON_AGENT = "👤 דברו עם נציג"
ALL_BUTTON_TEXTS = [BUTTON_PRICE_LIST, BUTTON_BOOKING, BUTTON_LOCATION, BUTTON_SAVE_CONTACT, BUTTON_AGENT]


async def _generate_answer_async(*args, **kwargs):
    return await asyncio.to_thread(generate_answer, *args, **kwargs)


async def _summarize_safe(user_id: str):
    """Run summarization in background without blocking the caller."""
    try:
        await asyncio.to_thread(maybe_summarize, user_id)
    except Exception as e:
        logger.error("Background summarization failed for user %s: %s", user_id, e)


async def _reply_html_safe(message, text: str, **kwargs):
    """שליחת הודעה עם HTML formatting, עם fallback לטקסט רגיל אם טלגרם דוחה."""
    if message is None:
        return None
    try:
        return await message.reply_text(text, parse_mode="HTML", **kwargs)
    except BadRequest:
        return await message.reply_text(text, **kwargs)


async def _send_html_safe(bot, chat_id: int, text: str, **kwargs):
    """שליחת הודעה עם HTML ל-chat_id, עם fallback לטקסט רגיל."""
    try:
        return await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", **kwargs)
    except BadRequest:
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)


def _get_main_keyboard() -> ReplyKeyboardMarkup:
    """Create the main menu keyboard with action buttons."""
    keyboard = [
        [KeyboardButton(BUTTON_PRICE_LIST), KeyboardButton(BUTTON_BOOKING)],
        [KeyboardButton(BUTTON_LOCATION), KeyboardButton(BUTTON_SAVE_CONTACT)],
        [KeyboardButton(BUTTON_AGENT)],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def _get_user_info(update: Update) -> tuple[str, str, str]:
    """Extract user ID, display name, and Telegram username (without @)."""
    user = update.effective_user
    user_id = str(user.id)
    display_name = user.full_name or (f"@{user.username}" if user.username else f"User {user.id}")
    telegram_username = user.username or ""
    return user_id, display_name, telegram_username


def _tg_handle(telegram_username: str) -> str:
    return f"@{telegram_username}" if telegram_username else ""


def _should_handoff_to_human(text: str) -> bool:
    """
    Detect model answers that indicate lack of knowledge and a handoff intent.
    """
    if not text:
        return False
    t = text.strip()
    if t == FALLBACK_RESPONSE.strip():
        return True
    # ניסוח נפוץ מכלל מספר 2 בפרומפט המערכת
    if "תנו לי להעביר" in t and "נציג אנושי" in t:
        return True
    return False


# ─── Follow-up Questions (שאלות המשך) ────────────────────────────────────

# קידומת callback_data לשאלות המשך — הטקסט מאוחסן ב-context.bot_data
FOLLOW_UP_CB_PREFIX = "followup_"

# זמן תפוגה (בשניות) לכפתורי שאלות המשך שלא נלחצו — מנקים כדי למנוע דליפת זיכרון
_FOLLOW_UP_TTL_SECONDS = 3600  # שעה


def _cleanup_stale_follow_ups(bot_data: dict) -> None:
    """ניקוי רשומות שאלות המשך ישנות מ-bot_data כדי למנוע צמיחה בלתי מוגבלת.

    שלב 1: אוסף מפתחות ישנים לרשימה נפרדת (stale_keys).
    שלב 2: מוחק מ-dict — בטוח כי לא עוברים על ה-dict בזמן שינוי.
    """
    now = int(time.time())
    stale_keys = []
    for key in bot_data:
        if not key.startswith(FOLLOW_UP_CB_PREFIX):
            continue
        # חילוץ ה-timestamp מהמפתח: followup_{user_id}_{timestamp}_{index}
        parts = key.split("_")
        try:
            ts = int(parts[-2])
            if now - ts > _FOLLOW_UP_TTL_SECONDS:
                stale_keys.append(key)
        except (ValueError, IndexError):
            continue
    for key in stale_keys:
        bot_data.pop(key, None)


def _build_follow_up_keyboard(questions: list[str], bot_data: dict, user_id: str) -> InlineKeyboardMarkup | None:
    """בניית מקלדת inline עם שאלות המשך.

    שומר את טקסט השאלה ב-bot_data כדי לאפשר שליפה ב-callback
    (callback_data מוגבל ל-64 בתים בטלגרם).
    המפתח כולל user_id למניעת התנגשויות בין משתמשים בו-זמניים.
    """
    if not questions:
        return None

    # ניקוי רשומות ישנות שלא נלחצו
    _cleanup_stale_follow_ups(bot_data)

    buttons = []
    now = int(time.time())
    for i, q in enumerate(questions):
        # מזהה ייחודי לכל שאלה — כולל user_id למניעת התנגשויות
        cb_id = f"{FOLLOW_UP_CB_PREFIX}{user_id}_{now}_{i}"
        bot_data[cb_id] = q
        buttons.append([InlineKeyboardButton(f"💡 {q}", callback_data=cb_id)])
    return InlineKeyboardMarkup(buttons)


async def _notify_owner(context: ContextTypes.DEFAULT_TYPE, text: str, max_retries: int = 3) -> bool:
    """שליחת התראה לבעל העסק עם retry ו-exponential backoff.

    מנסה עד max_retries פעמים במקרה של שגיאות רשת זמניות (TimedOut, NetworkError).
    שגיאות אחרות (למשל chat_id לא תקין) גורמות לכשלון מיידי — אין טעם לנסות שוב.
    """
    if not TELEGRAM_OWNER_CHAT_ID:
        return False

    for attempt in range(max_retries):
        try:
            await context.bot.send_message(
                chat_id=TELEGRAM_OWNER_CHAT_ID, text=text,
            )
            return True
        except (TimedOut, NetworkError) as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
                logger.warning("Owner notification retry %d/%d: %s", attempt + 1, max_retries, e)
            else:
                logger.error("Owner notification failed after %d attempts: %s", max_retries, e)
        except Exception as e:
            logger.error("Owner notification unexpected error: %s", e)
            return False
    return False


async def _create_request_and_notify_owner(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    display_name: str,
    telegram_username: str,
    message: str,
) -> int:
    request_id = db.create_agent_request(
        user_id,
        display_name,
        message=message,
        telegram_username=telegram_username,
    )

    handle = _tg_handle(telegram_username) or "(ללא שם משתמש)"
    notification = (
        f"🔔 בקשת נציג #{request_id}\n\n"
        f"לקוח: {display_name}\n"
        f"יוזר: {handle}\n"
        f"זמן: עכשיו\n\n"
        f"{message}"
    )
    await _notify_owner(context, notification)

    return request_id


async def _handoff_to_human(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    display_name: str,
    telegram_username: str,
    reason: str,
    *,
    chat_id: int | None = None,
) -> None:
    await _create_request_and_notify_owner(
        context,
        user_id=user_id,
        display_name=display_name,
        telegram_username=telegram_username,
        message=reason,
    )

    response_text = FALLBACK_RESPONSE
    db.save_message(user_id, display_name, "assistant", response_text)
    # callback queries לא מספקים update.message — שליחה ישירה לצ'אט
    if chat_id is not None and update.message is None:
        await context.bot.send_message(
            chat_id=chat_id,
            text=response_text,
            reply_markup=_get_main_keyboard(),
        )
    else:
        await update.message.reply_text(
            response_text,
            reply_markup=_get_main_keyboard(),
        )


# ─── /start Command ──────────────────────────────────────────────────────────

@rate_limit_guard
@live_chat_guard
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command — send welcome message with menu.

    אם ה-deep link מכיל פרמטר ref_XXX — נרשום את ההפניה.
    """
    user_id, display_name, _telegram_username = _get_user_info(update)

    # רישום המשתמש כמנוי שידורים (אם עוד לא קיים)
    db.ensure_user_subscribed(user_id)

    # זיהוי קוד הפניה מה-deep link: /start REF_XXXXXXXX
    referral_registered = False
    if context.args:
        arg = context.args[0]
        if arg.startswith("REF_"):
            referral_registered = db.register_referral(arg, user_id)
            if referral_registered:
                logger.info("Referral registered: user %s via code %s", user_id, arg)

    # _html.escape לערכי קונפיג בודדים; sanitize_telegram_html לפלט LLM שלם
    welcome_text = (
        f"👋 ברוכים הבאים ל-<b>{_html.escape(BUSINESS_NAME)}</b>!\n\n"
        f"אני העוזר הווירטואלי שלכם. אני יכול לעזור לכם עם:\n"
        f"• מידע על השירותים והמחירים שלנו\n"
        f"• בקשת תורים\n"
        f"• מענה על שאלות\n"
        f"• חיבור לנציג אנושי\n\n"
        f"פשוט כתבו את השאלה שלכם או השתמשו בכפתורים למטה! 👇"
    )

    if referral_registered:
        welcome_text += (
            "\n\n🎁 <b>הגעתם דרך הפניה!</b> "
            "לאחר שתקבעו ותשלימו את התור הראשון שלכם — "
            "גם אתם וגם החבר/ה שהפנה אתכם תקבלו <b>10% הנחה לחודשיים!</b>"
        )

    await update.message.reply_text(
        welcome_text,
        parse_mode="HTML",
        reply_markup=_get_main_keyboard()
    )

    # Log the interaction
    db.save_message(user_id, display_name, "user", "/start")
    db.save_message(user_id, display_name, "assistant", "[Welcome message sent]")


# ─── /stop Command (ביטול הרשמה לשידורים) ────────────────────────────────────

@rate_limit_guard
@live_chat_guard
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """טיפול בפקודת /stop — ביטול הרשמה לקבלת הודעות שידור."""
    user_id, display_name, _ = _get_user_info(update)

    if not db.is_user_subscribed(user_id):
        await update.message.reply_text(
            "ההרשמה שלכם כבר בוטלה. לא תקבלו הודעות שידור.\n"
            "כדי להירשם מחדש, שלחו /subscribe",
            reply_markup=_get_main_keyboard(),
        )
        return

    db.unsubscribe_user(user_id)
    db.save_message(user_id, display_name, "user", "/stop")
    db.save_message(user_id, display_name, "assistant", "[ביטול הרשמה לשידורים]")

    await update.message.reply_text(
        "✅ ההרשמה שלכם לקבלת הודעות שידור בוטלה.\n"
        "תמשיכו לקבל תשובות רגילות מהבוט.\n\n"
        "כדי להירשם מחדש, שלחו /subscribe",
        reply_markup=_get_main_keyboard(),
    )


# ─── /subscribe Command (הרשמה מחדש לשידורים) ────────────────────────────────

@rate_limit_guard
@live_chat_guard
async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """טיפול בפקודת /subscribe — הרשמה מחדש לקבלת שידורים."""
    user_id, display_name, _ = _get_user_info(update)

    if db.is_user_subscribed(user_id):
        await update.message.reply_text(
            "אתם כבר רשומים לקבלת הודעות שידור.",
            reply_markup=_get_main_keyboard(),
        )
        return

    db.resubscribe_user(user_id)
    db.save_message(user_id, display_name, "user", "/subscribe")
    db.save_message(user_id, display_name, "assistant", "[הרשמה מחדש לשידורים]")

    await update.message.reply_text(
        "✅ נרשמתם מחדש לקבלת הודעות שידור!\n"
        "כדי לבטל בכל עת, שלחו /stop",
        reply_markup=_get_main_keyboard(),
    )


# ─── /help Command ───────────────────────────────────────────────────────────

@rate_limit_guard
@live_chat_guard
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /help command."""
    user_id, display_name, _ = _get_user_info(update)

    help_text = (
        "🤖 <b>איך להשתמש בבוט:</b>\n\n"
        "• פשוט כתבו כל שאלה ואעשה כמיטב יכולתי לענות!\n"
        "• לחצו על <b>📋 מחירון</b> כדי לראות את השירותים והמחירים\n"
        "• לחצו על <b>📅 בקשת תור</b> כדי לבקש תור\n"
        "• לחצו על <b>📍 שליחת מיקום</b> כדי לקבל את הכתובת והמפה שלנו\n"
        "• לחצו על <b>📇 שמור איש קשר</b> כדי לשמור אותנו באנשי הקשר\n"
        "• לחצו על <b>👤 דברו עם נציג</b> כדי לדבר עם נציג אמיתי\n\n"
        "אפשר גם לשאול שאלות כמו:\n"
        '  <i>"מה שעות הפתיחה שלכם?"</i>\n'
        '  <i>"האם אתם מציעים צביעת שיער?"</i>\n'
        '  <i>"מה מדיניות הביטולים שלכם?"</i>'
    )

    await update.message.reply_text(
        help_text,
        parse_mode="HTML",
        reply_markup=_get_main_keyboard()
    )


# ─── Price List Button ───────────────────────────────────────────────────────

async def _price_list_core(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """לוגיקה פנימית של מחירון — ללא דקורטורים."""
    user_id, display_name, telegram_username = _get_user_info(update)

    await update.message.reply_text("📋 תנו לי רגע לחפש את המחירון שלנו...")

    await _handle_rag_query(
        update, context,
        user_id=user_id,
        display_name=display_name,
        telegram_username=telegram_username,
        user_message="📋 מחירון",
        query="הצג לי את המחירון המלא עם כל השירותים והמחירים",
        handoff_reason="הלקוח ביקש מחירון, אך אין מידע זמין במאגר.",
    )


@rate_limit_guard
@live_chat_guard
async def price_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Price List button — retrieve pricing info from KB."""
    return await _price_list_core(update, context)

# גרסה ללא rate_limit — לניתוב פנימי
@live_chat_guard
async def _price_list_skip_ratelimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _price_list_core(update, context)


# ─── Send Location Button ────────────────────────────────────────────────────

async def _location_core(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """לוגיקה פנימית של מיקום — ללא דקורטורים."""
    user_id, display_name, telegram_username = _get_user_info(update)

    await _handle_rag_query(
        update, context,
        user_id=user_id,
        display_name=display_name,
        telegram_username=telegram_username,
        user_message="📍 מיקום",
        query="מה הכתובת והמיקום של העסק? איך מגיעים?",
        handoff_reason="הלקוח ביקש לקבל מיקום/כתובת, אך אין מידע זמין במאגר.",
    )


@rate_limit_guard
@live_chat_guard
async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Send Location button — send business location info."""
    return await _location_core(update, context)

# גרסה ללא rate_limit — לניתוב פנימי
@live_chat_guard
async def _location_skip_ratelimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _location_core(update, context)


# ─── Save Contact (vCard) Button ─────────────────────────────────────────────

def _vcard_escape(value: str) -> str:
    """Escape לתווים מיוחדים ב-vCard לפי RFC 6350 — backslash, נקודה-פסיק ופסיק."""
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")


def _generate_vcard_text() -> str:
    """יצירת טקסט vCard מפרטי העסק שבקונפיגורציה."""
    # בניית סיכום שעות מטבלת business_hours
    hours_parts = []
    all_hours = db.get_all_business_hours()
    day_abbr = {0: "Su", 1: "Mo", 2: "Tu", 3: "We", 4: "Th", 5: "Fr", 6: "Sa"}
    for h in all_hours:
        if not h["is_closed"]:
            d = day_abbr.get(h["day_of_week"], "?")
            hours_parts.append(f"{d} {h['open_time']}-{h['close_time']}")
    hours_summary = " | ".join(hours_parts) if hours_parts else ""

    escaped_name = _vcard_escape(BUSINESS_NAME)

    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"FN:{escaped_name}",
        f"N:{escaped_name};;;;",
        f"ORG:{escaped_name}",
    ]
    if BUSINESS_PHONE:
        lines.append(f"TEL;TYPE=WORK,VOICE:{BUSINESS_PHONE}")
    if BUSINESS_ADDRESS:
        lines.append(f"ADR;TYPE=WORK:;;{_vcard_escape(BUSINESS_ADDRESS)};;;;")
    if BUSINESS_WEBSITE:
        lines.append(f"URL:{BUSINESS_WEBSITE}")
    if hours_summary:
        lines.append(f"NOTE:{_vcard_escape(hours_summary)}")
    lines.append("END:VCARD")
    return "\r\n".join(lines)


async def _save_contact_core(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """לוגיקה פנימית של שמירת איש קשר — ללא דקורטורים."""
    user_id, display_name, _ = _get_user_info(update)

    vcard_content = _generate_vcard_text()
    vcard_file = BytesIO(vcard_content.encode("utf-8"))
    vcard_file.name = f"{BUSINESS_NAME}.vcf"

    db.save_message(user_id, display_name, "user", "📇 שמירת איש קשר")

    await update.message.reply_document(
        document=vcard_file,
        caption="הנה כרטיס הביקור שלנו! לחצו עליו ושמרו באנשי הקשר. 👇",
        reply_markup=_get_main_keyboard(),
    )

    db.save_message(user_id, display_name, "assistant", "[כרטיס ביקור נשלח]")


@rate_limit_guard
@live_chat_guard
async def save_contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """שליחת כרטיס ביקור דיגיטלי (vCard) כקובץ .vcf."""
    return await _save_contact_core(update, context)

# גרסה ללא rate_limit — לניתוב פנימי
@live_chat_guard
async def _save_contact_skip_ratelimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _save_contact_core(update, context)


# ─── Talk to Agent Button ────────────────────────────────────────────────────

async def _talk_to_agent_core(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """לוגיקה פנימית של בקשת נציג — ללא דקורטורים, משמשת את שני הניתובים."""
    user_id, display_name, telegram_username = _get_user_info(update)

    # אם הגענו מזיהוי intent — ההודעה כבר נשמרה ב-message_handler, לא שומרים שוב
    real_message = context.user_data.get("_agent_real_message")
    skip_user_save = real_message is not None

    # Create agent request in database
    # אם הגענו מ-intent detection — נעביר לבעל העסק את ההודעה המקורית של הלקוח
    agent_msg = (
        f"הלקוח ביקש נציג: {real_message}"
        if real_message
        else "הלקוח מבקש לדבר עם נציג אנושי."
    )
    await _create_request_and_notify_owner(
        context,
        user_id=user_id,
        display_name=display_name,
        telegram_username=telegram_username,
        message=agent_msg,
    )

    response_text = (
        "👤 הודעתי לצוות שלנו שאתם מעוניינים לדבר עם מישהו.\n\n"
        "נציג אנושי יחזור אליכם בקרוב. "
        "בינתיים, אתם מוזמנים לשאול אותי כל שאלה נוספת!"
    )

    if not skip_user_save:
        db.save_message(user_id, display_name, "user", "👤 שיחה עם נציג")
    db.save_message(user_id, display_name, "assistant", response_text)

    await update.message.reply_text(
        response_text,
        reply_markup=_get_main_keyboard()
    )


# גרסה מלאה — עם כל הדקורטורים, לשימוש כ-handler ראשי
@vacation_guard_agent
@rate_limit_guard
@live_chat_guard
async def talk_to_agent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Talk to Agent button — notify the business owner."""
    return await _talk_to_agent_core(update, context)

# גרסה ללא rate_limit — לניתוב פנימי מ-message_handler (שכבר עבר rate limit)
@vacation_guard_agent
@live_chat_guard
async def _talk_to_agent_skip_ratelimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ניתוב פנימי — מדלג על rate_limit (הקורא כבר עבר אותו)."""
    return await _talk_to_agent_core(update, context)


# ─── Appointment Booking Flow ────────────────────────────────────────────────

async def _booking_start_core(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """לוגיקה פנימית של התחלת תור — ללא דקורטורים, משמשת את שני הניתובים."""
    user_id, display_name, telegram_username = _get_user_info(update)

    # Log the user's booking attempt even if we handoff to human.
    db.save_message(user_id, display_name, "user", "📅 בקשת תור")

    # Get available services from KB
    result = await _generate_answer_async("אילו שירותים אתם מציעים? פרטו בקצרה.")

    stripped = strip_source_citation(result["answer"])
    if _should_handoff_to_human(stripped):
        await _handoff_to_human(
            update,
            context,
            user_id=user_id,
            display_name=display_name,
            telegram_username=telegram_username,
            reason="הלקוח ביקש לקבוע תור, אך אין מידע זמין על השירותים במאגר.",
        )
        return ConversationHandler.END

    stripped = sanitize_telegram_html(stripped)
    text = (
        "📅 <b>בקשת תור</b>\n\n"
        f"{stripped}\n\n"
        "אנא כתבו את <b>השירות</b> שתרצו להזמין "
        "(או הקלידו /cancel כדי לחזור):"
    )

    await _reply_html_safe(update.message, text)
    return BOOKING_SERVICE


# גרסה מלאה — עם כל הדקורטורים, לשימוש כ-entry point של ConversationHandler
@vacation_guard_booking
@rate_limit_guard_booking
@live_chat_guard_booking
async def booking_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the appointment booking conversation."""
    return await _booking_start_core(update, context)

# גרסה ללא rate_limit — לניתוב פנימי מ-booking_button_interrupt (שכבר עבר rate limit)
@vacation_guard_booking
@live_chat_guard_booking
async def _booking_start_skip_ratelimit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """ניתוב פנימי — מדלג על rate_limit (הקורא כבר עבר אותו)."""
    return await _booking_start_core(update, context)


@rate_limit_guard_booking
@live_chat_guard_booking
async def booking_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the service selection."""
    context.user_data["booking_service"] = update.message.text

    await update.message.reply_text(
        "📆 מעולה! באיזה <b>תאריך</b> תעדיפו?\n"
        "(לדוגמה, 'יום שני', '15 במרץ', 'מחר')\n\n"
        "הקלידו /cancel כדי לחזור.",
        parse_mode="HTML"
    )
    return BOOKING_DATE


@rate_limit_guard_booking
@live_chat_guard_booking
async def booking_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the preferred date."""
    user_text = update.message.text
    context.user_data["booking_date"] = user_text

    # אימות רך — אם לא זוהה תאריך מובנה, מוסיפים רמז (לא חוסמים)
    dates = extract_dates(user_text)
    hint = ""
    if not dates:
        hint = "\n\n💡 <i>טיפ: ניתן לכתוב תאריך כמו 15/03 או '14 במרץ'.</i>"

    await _reply_html_safe(
        update.message,
        "🕐 איזו <b>שעה</b> מתאימה לכם?\n"
        "(לדוגמה, '10:00', 'אחר הצהריים', '14:00')\n\n"
        "הקלידו /cancel כדי לחזור."
        + hint,
    )
    return BOOKING_TIME


@rate_limit_guard_booking
@live_chat_guard_booking
async def booking_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the preferred time and show confirmation."""
    context.user_data["booking_time"] = update.message.text

    service = _html.escape(context.user_data.get("booking_service", ""))
    date = _html.escape(context.user_data.get("booking_date", ""))
    preferred_time = _html.escape(context.user_data.get("booking_time", ""))

    confirmation_text = (
        "📋 <b>סיכום בקשת התור:</b>\n\n"
        f"• שירות: {service}\n"
        f"• תאריך: {date}\n"
        f"• שעה: {preferred_time}\n\n"
        "אנא אשרו על ידי כתיבת <b>כן</b> או <b>לא</b>:"
    )

    await _reply_html_safe(update.message, confirmation_text)
    return BOOKING_CONFIRM


@rate_limit_guard_booking
@live_chat_guard_booking
async def booking_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle booking confirmation."""
    user_id, display_name, telegram_username = _get_user_info(update)
    answer = update.message.text.lower().strip()
    
    if answer in ("yes", "y", "confirm", "כן", "אישור"):
        service = context.user_data.get("booking_service", "")
        date = context.user_data.get("booking_date", "")
        preferred_time = context.user_data.get("booking_time", "")

        # Save appointment to database
        appt_id = db.create_appointment(
            user_id=user_id,
            username=display_name,
            service=service,
            preferred_date=date,
            preferred_time=preferred_time,
            telegram_username=telegram_username,
        )

        # Notify business owner
        handle = _tg_handle(telegram_username) or "(ללא שם משתמש)"
        notification = (
            f"📅 בקשת תור חדשה לאישור #{appt_id}\n\n"
            f"לקוח: {display_name}\n"
            f"יוזר: {handle}\n"
            f"שירות: {service}\n"
            f"תאריך: {date}\n"
            f"שעה: {preferred_time}\n"
        )
        await _notify_owner(context, notification)

        db.save_message(user_id, display_name, "assistant",
                        f"בקשת תור: {service} בתאריך {date} בשעה {preferred_time}")

        await update.message.reply_text(
            f"📋 בקשת התור התקבלה!\n\n"
            f"• שירות: {service}\n"
            f"• תאריך: {date}\n"
            f"• שעה: {preferred_time}\n\n"
            f"העברנו את הפרטים לבית העסק. "
            f"ניצור איתכם קשר בהקדם לאישור סופי של השעה.",
            reply_markup=_get_main_keyboard()
        )

        # קוד הפניה נשלח רק כשהתור מאושר ע"י בעל העסק (ב-admin)
    else:
        await update.message.reply_text(
            "❌ בקשת התור בוטלה. אין בעיה!\n"
            "אתם מוזמנים לבקש תור חדש בכל עת.",
            reply_markup=_get_main_keyboard()
        )
    
    context.user_data.clear()
    return ConversationHandler.END


@rate_limit_guard_booking
@live_chat_guard_booking
async def booking_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the booking flow."""
    context.user_data.clear()
    await update.message.reply_text(
        "תהליך בקשת התור בוטל. איך עוד אפשר לעזור לכם?",
        reply_markup=_get_main_keyboard()
    )
    return ConversationHandler.END


@rate_limit_guard_booking
@live_chat_guard_booking
async def booking_button_interrupt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle button clicks during an active booking — cancel booking and route to the clicked button."""
    context.user_data.clear()
    user_message = update.message.text

    # מדלגים על rate_limit (הקורא כבר עבר אותו) אבל שומרים על
    # live_chat_guard (ו-vacation_guard היכן שרלוונטי) דרך גרסאות _skip_ratelimit.
    if user_message == BUTTON_BOOKING:
        return await _booking_start_skip_ratelimit(update, context)

    if user_message == BUTTON_PRICE_LIST:
        await _price_list_skip_ratelimit(update, context)
    elif user_message == BUTTON_LOCATION:
        await _location_skip_ratelimit(update, context)
    elif user_message == BUTTON_SAVE_CONTACT:
        await _save_contact_skip_ratelimit(update, context)
    elif user_message == BUTTON_AGENT:
        await _talk_to_agent_skip_ratelimit(update, context)
    else:
        # Safety fallback — should not happen, but avoid a silent dead-end
        logger.warning("booking_button_interrupt: unexpected text %r", user_message)
        await update.message.reply_text(
            "תהליך בקשת התור בוטל. איך עוד אפשר לעזור לכם?",
            reply_markup=_get_main_keyboard(),
        )

    return ConversationHandler.END


# ─── Shared RAG pipeline ─────────────────────────────────────────────────────

async def _handle_rag_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: str,
    display_name: str,
    telegram_username: str,
    user_message: str,
    query: str,
    handoff_reason: str,
    chat_id: int | None = None,
) -> None:
    """הרצת צינור RAG + LLM ושליחת התוצאה (או העברה לנציג).

    כש-chat_id מסופק ו-update.message לא קיים (למשל callback query),
    השליחה נעשית ישירות לצ'אט במקום כ-reply.
    """
    effective_chat_id = chat_id or update.effective_chat.id
    use_direct_send = chat_id is not None and update.message is None

    await context.bot.send_chat_action(chat_id=effective_chat_id, action="typing")

    history = db.get_conversation_history(user_id, limit=CONTEXT_WINDOW_SIZE)
    db.save_message(user_id, display_name, "user", user_message)

    result = await _generate_answer_async(
        user_query=query,
        conversation_history=history,
        user_id=user_id,
        username=display_name,
    )

    stripped = strip_source_citation(result["answer"])
    if _should_handoff_to_human(stripped):
        # אסקלציה הדרגתית — לא מעבירים לנציג מיד בכישלון ראשון
        fallback_count = context.user_data.get("consecutive_fallbacks", 0) + 1
        context.user_data["consecutive_fallbacks"] = fallback_count

        if fallback_count == 1:
            # ניסיון ראשון — הצעה לנסח מחדש, בלי agent request
            soft_msg = "לא הצלחתי למצוא תשובה מדויקת. אפשר לנסח את השאלה אחרת?"
            db.save_message(user_id, display_name, "assistant", soft_msg)
            if use_direct_send:
                await _send_html_safe(context.bot, effective_chat_id, soft_msg)
            else:
                await _reply_html_safe(update.message, soft_msg)
        elif fallback_count == 2:
            # ניסיון שני — תפריט ראשי + הצעת נציג
            menu_msg = (
                "עדיין לא מצאתי תשובה מתאימה.\n"
                "הנה כמה אפשרויות שאולי יעזרו, "
                "או לחצו על <b>👤 דברו עם נציג</b>:"
            )
            db.save_message(user_id, display_name, "assistant", menu_msg)
            if use_direct_send:
                await _send_html_safe(context.bot, effective_chat_id, menu_msg, reply_markup=_get_main_keyboard())
            else:
                await _reply_html_safe(update.message, menu_msg, reply_markup=_get_main_keyboard())
        else:
            # ניסיון שלישי+ — העברה לנציג (התנהגות קיימת)
            context.user_data["consecutive_fallbacks"] = 0
            await _handoff_to_human(
                update, context,
                user_id=user_id,
                display_name=display_name,
                telegram_username=telegram_username,
                reason=handoff_reason,
                chat_id=effective_chat_id,
            )
    else:
        # תשובה מוצלחת — איפוס מונה fallbacks רצופים
        context.user_data["consecutive_fallbacks"] = 0
        db.save_message(user_id, display_name, "assistant", result["answer"], ", ".join(result["sources"]))
        sanitized = sanitize_telegram_html(stripped)
        if use_direct_send:
            await _send_html_safe(context.bot, effective_chat_id, sanitized, reply_markup=_get_main_keyboard())
        else:
            await _reply_html_safe(update.message, sanitized, reply_markup=_get_main_keyboard())

        # שאלות המשך — שליחה כהודעה נפרדת עם כפתורי inline
        follow_up_qs = result.get("follow_up_questions", [])
        if FOLLOW_UP_ENABLED and follow_up_qs:
            follow_up_kb = _build_follow_up_keyboard(follow_up_qs, context.bot_data, user_id)
            if follow_up_kb:
                if use_direct_send:
                    await _send_html_safe(
                        context.bot, effective_chat_id,
                        "💡 <b>אולי תרצו גם לשאול:</b>",
                        reply_markup=follow_up_kb,
                    )
                else:
                    await update.message.reply_text(
                        "💡 <b>אולי תרצו גם לשאול:</b>",
                        parse_mode="HTML",
                        reply_markup=follow_up_kb,
                    )

    context.application.create_task(_summarize_safe(user_id))


# ─── Free-Text Message Handler ───────────────────────────────────────────────

@rate_limit_guard
@live_chat_guard
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle any free-text message from the user.

    Intent detection is applied first so that simple messages (greetings,
    farewells, booking requests) are routed without an expensive RAG + LLM
    round-trip.  Only GENERAL and PRICING intents go through the RAG pipeline.
    """
    user_id, display_name, telegram_username = _get_user_info(update)
    user_message = update.message.text

    # רישום המשתמש כמנוי שידורים (אם עוד לא קיים)
    db.ensure_user_subscribed(user_id)

    # בדיקת מעורבות גבוהה — רץ ברקע על כל סוגי ההודעות (כולל ברכות,
    # כפתורים, תורים וכו'). הבדיקה עצמה זולה (early exit אם כבר נשלח).
    context.application.create_task(
        _check_high_engagement_referral(update, user_id)
    )

    # ניתוב כפתורים — מדלגים על rate_limit (כבר נספר פעם אחת) אבל
    # שומרים על live_chat_guard (ו-vacation_guard היכן שרלוונטי).
    # איפוס מונה fallbacks — לחיצת כפתור = המשתמש התקדם, לא צריך לספור fallback
    if user_message == BUTTON_PRICE_LIST:
        context.user_data["consecutive_fallbacks"] = 0
        return await _price_list_skip_ratelimit(update, context)
    elif user_message == BUTTON_LOCATION:
        context.user_data["consecutive_fallbacks"] = 0
        return await _location_skip_ratelimit(update, context)
    elif user_message == BUTTON_SAVE_CONTACT:
        context.user_data["consecutive_fallbacks"] = 0
        return await _save_contact_skip_ratelimit(update, context)
    elif user_message == BUTTON_AGENT:
        context.user_data["consecutive_fallbacks"] = 0
        return await _talk_to_agent_skip_ratelimit(update, context)

    # ── Intent Detection ──────────────────────────────────────────────────
    intent = detect_intent(user_message)

    # איפוס מונה fallbacks רצופים בכל intent שאינו GENERAL/PRICING (שעוברים RAG).
    # ה-RAG path מאפס בעצמו בתוך _handle_rag_query כשהתשובה מוצלחת.
    if intent not in (Intent.GENERAL, Intent.PRICING, Intent.LOCATION):
        context.user_data["consecutive_fallbacks"] = 0

    # Greeting / Farewell — respond directly, no RAG needed
    if intent in (Intent.GREETING, Intent.FAREWELL):
        db.save_message(user_id, display_name, "user", user_message)
        response = get_direct_response(intent)
        db.save_message(user_id, display_name, "assistant", response)
        await update.message.reply_text(response, reply_markup=_get_main_keyboard())
        return

    # Business hours — respond with live status, no RAG needed
    if intent == Intent.BUSINESS_HOURS:
        db.save_message(user_id, display_name, "user", user_message)
        status = is_currently_open()
        schedule = get_weekly_schedule_text()
        response = f"{status['message']}\n\n{schedule}"
        db.save_message(user_id, display_name, "assistant", response)
        await update.message.reply_text(response, reply_markup=_get_main_keyboard())
        return

    # Appointment booking — guide the user to the booking button so the
    # ConversationHandler state machine is properly engaged.  Calling
    # booking_start() directly from here would bypass the ConversationHandler
    # entry points, breaking the multi-step booking flow.
    if intent == Intent.APPOINTMENT_BOOKING:
        db.save_message(user_id, display_name, "user", user_message)
        # בזמן חופשה — הודעת חופשה במקום הפניה לכפתור תורים
        if VacationService.is_active():
            response = VacationService.get_booking_message()
            db.save_message(user_id, display_name, "assistant", response)
            await update.message.reply_text(response, reply_markup=_get_main_keyboard())
            return
        response = (
            "אשמח לעזור לכם לבקש תור! 📅\n\n"
            "לחצו על הכפתור <b>📅 בקשת תור</b> למטה כדי להתחיל."
        )
        db.save_message(user_id, display_name, "assistant", response)
        await _reply_html_safe(
            update.message, response, reply_markup=_get_main_keyboard()
        )
        return

    # Appointment cancellation — ask the user to confirm before taking action
    if intent == Intent.APPOINTMENT_CANCEL:
        db.save_message(user_id, display_name, "user", user_message)
        confirm_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("כן, לבטל", callback_data="cancel_appt_yes"),
                InlineKeyboardButton("לא, טעות", callback_data="cancel_appt_no"),
            ]
        ])
        confirm_text = "האם אתם בטוחים שתרצו לבטל את התור?"
        db.save_message(user_id, display_name, "assistant", confirm_text)
        await update.message.reply_text(confirm_text, reply_markup=confirm_kb)
        return

    # Human agent — בקשה מפורשת לנציג.
    # בזמן חופשה — הודעת חופשה (כמו APPOINTMENT_BOOKING), כולל שמירה ב-DB.
    # אחרת — מפעיל את לוגיקת הנציג עם ההודעה האמיתית.
    if intent == Intent.HUMAN_AGENT:
        db.save_message(user_id, display_name, "user", user_message)
        if VacationService.is_active():
            response = VacationService.get_agent_message()
            db.save_message(user_id, display_name, "assistant", response)
            await update.message.reply_text(response, reply_markup=_get_main_keyboard())
            return
        context.user_data["_agent_real_message"] = user_message
        try:
            return await _talk_to_agent_skip_ratelimit(update, context)
        finally:
            context.user_data.pop("_agent_real_message", None)

    # Complaint — לקוח מתוסכל, מציעים נציג אנושי (I1)
    if intent == Intent.COMPLAINT:
        db.save_message(user_id, display_name, "user", user_message)
        response = (
            "אנחנו מצטערים לשמוע שהחוויה לא הייתה טובה. 😔\n"
            "נשמח לטפל בפנייתכם באופן אישי.\n\n"
            'לחצו על <b>👤 דברו עם נציג</b> למטה כדי שנציג אנושי יחזור אליכם בהקדם.'
        )
        db.save_message(user_id, display_name, "assistant", response)
        await _reply_html_safe(
            update.message, response, reply_markup=_get_main_keyboard()
        )
        return

    # Location — שאלות על מיקום וכתובת, ממוקד דרך RAG (I3)
    if intent == Intent.LOCATION:
        db.save_message(user_id, display_name, "user", user_message)
        await _handle_rag_query(
            update, context,
            user_id=user_id,
            display_name=display_name,
            telegram_username=telegram_username,
            user_message=user_message,
            query="מיקום כתובת הגעה: " + user_message,
            handoff_reason=f"הלקוח שאל על מיקום: {user_message}",
        )
        return

    # ── Pricing / General — both go through the RAG pipeline ────────────
    query = ("מחירון: " + user_message) if intent == Intent.PRICING else user_message
    handoff_reason = (
        f"הלקוח שאל על מחירים: {user_message}" if intent == Intent.PRICING
        else f"הלקוח ביקש עזרה בנושא: {user_message}"
    )
    await _handle_rag_query(
        update, context,
        user_id=user_id,
        display_name=display_name,
        telegram_username=telegram_username,
        user_message=user_message,
        query=query,
        handoff_reason=handoff_reason,
    )


# ─── Cancellation Confirmation Callback ──────────────────────────────────────

async def cancel_appointment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the inline-button response to the cancellation confirmation prompt.

    callback query handler — חייבים לקרוא ל-query.answer() לפני כל בדיקה
    אחרת, כי דקורטורים (rate_limit_guard, live_chat_guard) יכולים לחזור מוקדם
    ולהשאיר את אינדיקטור הטעינה של טלגרם תקוע. לכן הבדיקות נעשות ידנית.
    """
    query = update.callback_query
    # תמיד לענות ל-callback query כדי לבטל את אינדיקטור הטעינה של טלגרם
    await query.answer()

    from ai_chatbot.live_chat_service import LiveChatService
    user = update.effective_user
    if LiveChatService.is_active(str(user.id)):
        return

    user_id, display_name, telegram_username = _get_user_info(update)

    if query.data == "cancel_appt_yes":
        await _create_request_and_notify_owner(
            context,
            user_id=user_id,
            display_name=display_name,
            telegram_username=telegram_username,
            message=f"הלקוח אישר ביטול תור.",
        )
        response = (
            "קיבלתי את בקשתכם לביטול התור. ✅\n\n"
            "העברתי את הבקשה לצוות שלנו — נציג יחזור אליכם בקרוב לאשר את הביטול."
        )
    else:
        response = "בסדר גמור, התור נשאר! 👍\nאיך עוד אפשר לעזור?"

    db.save_message(user_id, display_name, "assistant", response)
    await query.edit_message_text(response)
    # Re-show the main keyboard via a follow-up message so the user keeps
    # the persistent reply keyboard visible after the inline button is resolved.
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="👇",
        reply_markup=_get_main_keyboard(),
    )


# ─── Referral System (מערכת הפניות) ──────────────────────────────────────

async def _maybe_send_referral_code(update: Update, user_id: str):
    """שליחת קוד הפניה אם המשתמש עדיין לא קיבל אחד.

    נקרא אחרי אישור תור או לאחר מעורבות גבוהה.
    הטקסט מגיע מ-referral_service (מקור אמת יחיד לבוט ולאדמין).
    נעילה אטומית ו-rollback בכישלון — כולל כשלון שקט (message=None).
    """
    from ai_chatbot.referral_service import get_referral_message_text

    code = db.generate_referral_code(user_id)
    if not code:
        return

    if not db.mark_referral_code_as_sent(user_id):
        return

    text = get_referral_message_text(code)
    success = False
    try:
        result = await _reply_html_safe(update.message, text)
        success = result is not None
    except Exception:
        logger.error("Exception sending referral code to user %s", user_id, exc_info=True)

    if not success:
        db.unmark_referral_code_sent(user_id)
        logger.error("Failed to send referral code to user %s, flag reset", user_id)


async def _check_high_engagement_referral(update: Update, user_id: str):
    """בדיקת מעורבות גבוהה — שליחת קוד הפניה אם המשתמש מאוד פעיל.

    תנאים (אחד מהם מספיק):
    - 10+ הודעות ב-30 הדקות האחרונות
    - 20+ הודעות ביום האחרון
    """
    # אם כבר נשלח קוד — לא צריך לבדוק
    if db.is_referral_code_sent(user_id):
        return

    if db.check_high_engagement(user_id):
        await _maybe_send_referral_code(update, user_id)


# ─── Follow-up Question Callback ─────────────────────────────────────────────

async def follow_up_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """טיפול בלחיצה על כפתור שאלת המשך — שולח את השאלה כאילו המשתמש הקליד אותה."""
    query = update.callback_query
    await query.answer()

    from ai_chatbot.live_chat_service import LiveChatService
    user = update.effective_user
    if LiveChatService.is_active(str(user.id)):
        return

    user_id, display_name, telegram_username = _get_user_info(update)

    # בדיקת rate limit — שאלות המשך צורכות קריאת LLM כמו הודעה רגילה
    limit_msg = check_rate_limit(user_id)
    if limit_msg is not None:
        try:
            await query.edit_message_text(limit_msg, parse_mode="HTML")
        except Exception:
            await query.edit_message_text(limit_msg)
        return

    cb_data = query.data
    # שליפת טקסט השאלה מ-bot_data (נתונים in-memory — נמחקים ברסטרט)
    question_text = context.bot_data.pop(cb_data, None)
    if not question_text:
        logger.warning("follow_up_callback: missing question for %s", cb_data)
        try:
            await query.edit_message_text("⏳ השאלה כבר לא זמינה. אפשר לשאול אותי ישירות!")
        except Exception as e:
            logger.error("Failed to edit expired follow-up message: %s", e)
        return

    # רישום rate limit רק אחרי שוידאנו שהשאלה קיימת
    record_message(user_id)

    chat_id = update.effective_chat.id

    # עדכון ההודעה המקורית — להראות איזו שאלה נבחרה
    try:
        await query.edit_message_text(f"💡 {question_text}")
    except Exception as e:
        logger.error("Failed to edit follow-up message: %s", e)

    # שימוש בצינור RAG המשותף
    await _handle_rag_query(
        update, context,
        user_id=user_id,
        display_name=display_name,
        telegram_username=telegram_username,
        user_message=question_text,
        query=question_text,
        handoff_reason=f"הלקוח שאל שאלת המשך: {question_text}",
        chat_id=chat_id,
    )


# ─── Error Handler ───────────────────────────────────────────────────────────

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors gracefully."""
    logger.error("Update %s caused error: %s", update, context.error)
    
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "מצטערים, משהו השתבש. אנא נסו שוב או לחצו על "
            "'👤 דברו עם נציג' כדי לדבר עם נציג אנושי.",
            reply_markup=_get_main_keyboard()
        )
