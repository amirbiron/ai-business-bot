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
import logging
from io import BytesIO
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler

from ai_chatbot import database as db
from ai_chatbot.llm import generate_answer, strip_source_citation, maybe_summarize
from ai_chatbot.intent import Intent, detect_intent, get_direct_response
from ai_chatbot.business_hours import is_currently_open, get_weekly_schedule_text
from ai_chatbot.config import (
    BUSINESS_NAME,
    BUSINESS_PHONE,
    BUSINESS_ADDRESS,
    BUSINESS_WEBSITE,
    TELEGRAM_OWNER_CHAT_ID,
    TELEGRAM_BOT_USERNAME,
    FALLBACK_RESPONSE,
    CONTEXT_WINDOW_SIZE,
)
from ai_chatbot.live_chat_service import live_chat_guard, live_chat_guard_booking
from ai_chatbot.rate_limiter import rate_limit_guard, rate_limit_guard_booking
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


async def _reply_markdown_safe(message, text: str, **kwargs):
    """
    Send a Markdown-formatted message, with a fallback to plain text if Telegram
    rejects invalid Markdown from model/user-provided content.
    """
    if message is None:
        return None
    try:
        return await message.reply_text(text, parse_mode="Markdown", **kwargs)
    except BadRequest:
        return await message.reply_text(text, **kwargs)


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
    # Common phrasing from SYSTEM_PROMPT rule #2
    if "תנו לי להעביר" in t and "נציג אנושי" in t:
        return True
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

    if TELEGRAM_OWNER_CHAT_ID:
        try:
            handle = _tg_handle(telegram_username) or "(ללא שם משתמש)"
            notification = (
                f"🔔 בקשת נציג #{request_id}\n\n"
                f"לקוח: {display_name}\n"
                f"יוזר: {handle}\n"
                f"זמן: עכשיו\n\n"
                f"{message}"
            )
            await context.bot.send_message(
                chat_id=TELEGRAM_OWNER_CHAT_ID,
                text=notification,
            )
        except Exception as e:
            logger.error("Failed to send owner notification: %s", e)

    return request_id


async def _handoff_to_human(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    display_name: str,
    telegram_username: str,
    reason: str,
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

    # זיהוי קוד הפניה מה-deep link: /start REF_XXXXXXXX
    referral_registered = False
    if context.args:
        arg = context.args[0]
        if arg.startswith("REF_"):
            referral_registered = db.register_referral(arg, user_id)
            if referral_registered:
                logger.info("Referral registered: user %s via code %s", user_id, arg)

    welcome_text = (
        f"👋 ברוכים הבאים ל-*{BUSINESS_NAME}*!\n\n"
        f"אני העוזר הווירטואלי שלכם. אני יכול לעזור לכם עם:\n"
        f"• מידע על השירותים והמחירים שלנו\n"
        f"• בקשת תורים\n"
        f"• מענה על שאלות\n"
        f"• חיבור לנציג אנושי\n\n"
        f"פשוט כתבו את השאלה שלכם או השתמשו בכפתורים למטה! 👇"
    )

    if referral_registered:
        welcome_text += (
            "\n\n🎁 *הגעתם דרך הפניה!* "
            "לאחר שתקבעו ותשלימו את התור הראשון שלכם — "
            "גם אתם וגם החבר/ה שהפנה אתכם תקבלו *10% הנחה לחודשיים!*"
        )

    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=_get_main_keyboard()
    )

    # Log the interaction
    db.save_message(user_id, display_name, "user", "/start")
    db.save_message(user_id, display_name, "assistant", "[Welcome message sent]")


# ─── /help Command ───────────────────────────────────────────────────────────

@rate_limit_guard
@live_chat_guard
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /help command."""
    user_id, display_name, _ = _get_user_info(update)

    help_text = (
        "🤖 *איך להשתמש בבוט:*\n\n"
        "• פשוט כתבו כל שאלה ואעשה כמיטב יכולתי לענות!\n"
        "• לחצו על *📋 מחירון* כדי לראות את השירותים והמחירים\n"
        "• לחצו על *📅 בקשת תור* כדי לבקש תור\n"
        "• לחצו על *📍 שליחת מיקום* כדי לקבל את הכתובת והמפה שלנו\n"
        "• לחצו על *📇 שמור איש קשר* כדי לשמור אותנו באנשי הקשר\n"
        "• לחצו על *👤 דברו עם נציג* כדי לדבר עם נציג אמיתי\n\n"
        "אפשר גם לשאול שאלות כמו:\n"
        '  _"מה שעות הפתיחה שלכם?"_\n'
        '  _"האם אתם מציעים צביעת שיער?"_\n'
        '  _"מה מדיניות הביטולים שלכם?"_'
    )
    
    await update.message.reply_text(
        help_text,
        parse_mode="Markdown",
        reply_markup=_get_main_keyboard()
    )


# ─── Price List Button ───────────────────────────────────────────────────────

@rate_limit_guard
@live_chat_guard
async def price_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Price List button — retrieve pricing info from KB."""
    user_id, display_name, telegram_username = _get_user_info(update)

    await update.message.reply_text("📋 תנו לי רגע לחפש את המחירון שלנו...")
    
    # Use the RAG pipeline to find pricing information
    result = await _generate_answer_async("הצג לי את המחירון המלא עם כל השירותים והמחירים")
    
    db.save_message(user_id, display_name, "user", "📋 מחירון")
    stripped = strip_source_citation(result["answer"])
    if _should_handoff_to_human(stripped):
        await _handoff_to_human(
            update,
            context,
            user_id=user_id,
            display_name=display_name,
            telegram_username=telegram_username,
            reason="הלקוח ביקש מחירון, אך אין מידע זמין במאגר.",
        )
        return

    db.save_message(user_id, display_name, "assistant", result["answer"], ", ".join(result["sources"]))

    await _reply_markdown_safe(
        update.message,
        stripped,
        reply_markup=_get_main_keyboard(),
    )


# ─── Send Location Button ────────────────────────────────────────────────────

@rate_limit_guard
@live_chat_guard
async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Send Location button — send business location info."""
    user_id, display_name, telegram_username = _get_user_info(update)

    # Use RAG to find location/address info
    result = await _generate_answer_async("מה הכתובת והמיקום של העסק? איך מגיעים?")
    
    db.save_message(user_id, display_name, "user", "📍 מיקום")

    stripped = strip_source_citation(result["answer"])
    if _should_handoff_to_human(stripped):
        await _handoff_to_human(
            update,
            context,
            user_id=user_id,
            display_name=display_name,
            telegram_username=telegram_username,
            reason="הלקוח ביקש לקבל מיקום/כתובת, אך אין מידע זמין במאגר.",
        )
        return

    db.save_message(user_id, display_name, "assistant", result["answer"], ", ".join(result["sources"]))

    await _reply_markdown_safe(
        update.message,
        stripped,
        reply_markup=_get_main_keyboard(),
    )


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


@rate_limit_guard
@live_chat_guard
async def save_contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """שליחת כרטיס ביקור דיגיטלי (vCard) כקובץ .vcf."""
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


# ─── Talk to Agent Button ────────────────────────────────────────────────────

@vacation_guard_agent
@rate_limit_guard
@live_chat_guard
async def talk_to_agent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Talk to Agent button — notify the business owner."""
    user_id, display_name, telegram_username = _get_user_info(update)

    # Create agent request in database
    await _create_request_and_notify_owner(
        context,
        user_id=user_id,
        display_name=display_name,
        telegram_username=telegram_username,
        message="הלקוח מבקש לדבר עם נציג אנושי.",
    )

    response_text = (
        "👤 הודעתי לצוות שלנו שאתם מעוניינים לדבר עם מישהו.\n\n"
        "נציג אנושי יחזור אליכם בקרוב. "
        "בינתיים, אתם מוזמנים לשאול אותי כל שאלה נוספת!"
    )

    db.save_message(user_id, display_name, "user", "👤 שיחה עם נציג")
    db.save_message(user_id, display_name, "assistant", response_text)

    await update.message.reply_text(
        response_text,
        reply_markup=_get_main_keyboard()
    )

# שרשרת ניתוב פנימי — מדלגת על rate_limit (הקורא כבר עבר אותו)
# אבל שומרת על vacation_guard + live_chat_guard.
_talk_to_agent_skip_ratelimit = vacation_guard_agent(
    talk_to_agent_handler.__wrapped__.__wrapped__
)


# ─── Appointment Booking Flow ────────────────────────────────────────────────

@vacation_guard_booking
@rate_limit_guard_booking
@live_chat_guard_booking
async def booking_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the appointment booking conversation."""
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

    text = (
        "📅 *בקשת תור*\n\n"
        f"{stripped}\n\n"
        "אנא כתבו את *השירות* שתרצו להזמין "
        "(או הקלידו /cancel כדי לחזור):"
    )

    await _reply_markdown_safe(update.message, text)
    return BOOKING_SERVICE

# שרשרת ניתוב פנימי — מדלגת על rate_limit (הקורא כבר עבר אותו)
# אבל שומרת על vacation_guard + live_chat_guard.
_booking_start_skip_ratelimit = vacation_guard_booking(
    booking_start.__wrapped__.__wrapped__
)


@rate_limit_guard_booking
@live_chat_guard_booking
async def booking_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the service selection."""
    context.user_data["booking_service"] = update.message.text

    await update.message.reply_text(
        "📆 מעולה! באיזה *תאריך* תעדיפו?\n"
        "(לדוגמה, 'יום שני', '15 במרץ', 'מחר')\n\n"
        "הקלידו /cancel כדי לחזור.",
        parse_mode="Markdown"
    )
    return BOOKING_DATE


@rate_limit_guard_booking
@live_chat_guard_booking
async def booking_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the preferred date."""
    context.user_data["booking_date"] = update.message.text

    await update.message.reply_text(
        "🕐 איזו *שעה* מתאימה לכם?\n"
        "(לדוגמה, '10:00', 'אחר הצהריים', '14:00')\n\n"
        "הקלידו /cancel כדי לחזור.",
        parse_mode="Markdown"
    )
    return BOOKING_TIME


@rate_limit_guard_booking
@live_chat_guard_booking
async def booking_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the preferred time and show confirmation."""
    context.user_data["booking_time"] = update.message.text

    service = context.user_data.get("booking_service", "")
    date = context.user_data.get("booking_date", "")
    time = context.user_data.get("booking_time", "")
    
    confirmation_text = (
        "📋 *סיכום בקשת התור:*\n\n"
        f"• שירות: {service}\n"
        f"• תאריך: {date}\n"
        f"• שעה: {time}\n\n"
        "אנא אשרו על ידי כתיבת *כן* או *לא*:"
    )
    
    await update.message.reply_text(confirmation_text, parse_mode="Markdown")
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
        time = context.user_data.get("booking_time", "")

        # Save appointment to database
        appt_id = db.create_appointment(
            user_id=user_id,
            username=display_name,
            service=service,
            preferred_date=date,
            preferred_time=time,
            telegram_username=telegram_username,
        )

        # Notify business owner
        if TELEGRAM_OWNER_CHAT_ID:
            try:
                handle = _tg_handle(telegram_username) or "(ללא שם משתמש)"
                notification = (
                    f"📅 בקשת תור חדשה לאישור #{appt_id}\n\n"
                    f"לקוח: {display_name}\n"
                    f"יוזר: {handle}\n"
                    f"שירות: {service}\n"
                    f"תאריך: {date}\n"
                    f"שעה: {time}\n"
                )
                await context.bot.send_message(
                    chat_id=TELEGRAM_OWNER_CHAT_ID,
                    text=notification,
                )
            except Exception as e:
                logger.error("Failed to send appointment notification: %s", e)

        db.save_message(user_id, display_name, "assistant",
                        f"בקשת תור: {service} בתאריך {date} בשעה {time}")

        await update.message.reply_text(
            f"📋 בקשת התור התקבלה!\n\n"
            f"• שירות: {service}\n"
            f"• תאריך: {date}\n"
            f"• שעה: {time}\n\n"
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
    # vacation_guard + live_chat_guard דרך ה-_skip_ratelimit references.
    # handlers ללא vacation guard (price_list, location) משתמשים ב-__wrapped__.
    if user_message == BUTTON_BOOKING:
        return await _booking_start_skip_ratelimit(update, context)

    if user_message == BUTTON_PRICE_LIST:
        await price_list_handler.__wrapped__(update, context)
    elif user_message == BUTTON_LOCATION:
        await location_handler.__wrapped__(update, context)
    elif user_message == BUTTON_SAVE_CONTACT:
        await save_contact_handler.__wrapped__(update, context)
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
) -> None:
    """Run the RAG + LLM pipeline and send the result (or hand off to a human)."""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

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
        await _handoff_to_human(
            update, context,
            user_id=user_id,
            display_name=display_name,
            telegram_username=telegram_username,
            reason=handoff_reason,
        )
    else:
        db.save_message(user_id, display_name, "assistant", result["answer"], ", ".join(result["sources"]))
        await _reply_markdown_safe(update.message, stripped, reply_markup=_get_main_keyboard())

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

    # ניתוב כפתורים — מדלגים על rate_limit (כבר נספר פעם אחת) אבל
    # שומרים על vacation_guard + live_chat_guard דרך _skip_ratelimit.
    if user_message == BUTTON_PRICE_LIST:
        return await price_list_handler.__wrapped__(update, context)
    elif user_message == BUTTON_LOCATION:
        return await location_handler.__wrapped__(update, context)
    elif user_message == BUTTON_SAVE_CONTACT:
        return await save_contact_handler.__wrapped__(update, context)
    elif user_message == BUTTON_AGENT:
        return await _talk_to_agent_skip_ratelimit(update, context)

    # ── Intent Detection ──────────────────────────────────────────────────
    intent = detect_intent(user_message)

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
            "לחצו על הכפתור *📅 בקשת תור* למטה כדי להתחיל."
        )
        db.save_message(user_id, display_name, "assistant", response)
        await _reply_markdown_safe(
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

    # בדיקת מעורבות גבוהה — שליחת קוד הפניה אם רלוונטי
    context.application.create_task(
        _check_high_engagement_referral(update, user_id)
    )


# ─── Cancellation Confirmation Callback ──────────────────────────────────────

async def cancel_appointment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the inline-button response to the cancellation confirmation prompt."""
    query = update.callback_query
    # Always answer the callback query first to dismiss Telegram's loading
    # indicator — the live chat guard cannot do this because it returns
    # before the handler body runs.
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

    נקרא אחרי בקשת תור ראשונה או לאחר מעורבות גבוהה.
    """
    # אם כבר קיים קוד — לא שולחים שוב (שליחה ראשונה בלבד)
    existing_code = db.get_user_referral_code(user_id)
    if existing_code:
        return

    code = db.generate_referral_code(user_id)
    if not code:
        return

    if TELEGRAM_BOT_USERNAME:
        link = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={code}"
    else:
        link = code

    referral_text = (
        "🎁 *רוצים לשתף עם חבר/ה?*\n\n"
        f"שלחו להם את הלינק הזה:\n{link}\n\n"
        "כשהם יקבעו וישלימו תור — *גם אתם וגם הם תקבלו 10% הנחה לחודשיים!*"
    )

    await _reply_markdown_safe(update.message, referral_text)


async def _check_high_engagement_referral(update: Update, user_id: str):
    """בדיקת מעורבות גבוהה — שליחת קוד הפניה אם המשתמש מאוד פעיל.

    תנאים (אחד מהם מספיק):
    - 10+ הודעות ב-30 הדקות האחרונות
    - 20+ הודעות ביום האחרון
    """
    # אם כבר יש קוד — לא צריך לבדוק
    if db.get_user_referral_code(user_id):
        return

    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    with db.get_connection() as conn:
        thirty_min_ago = (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        one_day_ago = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        # תנאי 1: 10+ הודעות ב-30 דקות
        row_30m = conn.execute(
            "SELECT COUNT(*) AS cnt FROM conversations WHERE user_id = ? AND role = 'user' AND created_at >= ?",
            (user_id, thirty_min_ago),
        ).fetchone()
        engaged_30m = row_30m and int(row_30m["cnt"]) >= 10

        # תנאי 2: 20+ הודעות ביום אחד
        row_1d = conn.execute(
            "SELECT COUNT(*) AS cnt FROM conversations WHERE user_id = ? AND role = 'user' AND created_at >= ?",
            (user_id, one_day_ago),
        ).fetchone()
        engaged_1d = row_1d and int(row_1d["cnt"]) >= 20

        if engaged_30m or engaged_1d:
            await _maybe_send_referral_code(update, user_id)


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
