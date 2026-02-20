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
from ai_chatbot.config import (
    BUSINESS_NAME,
    TELEGRAM_OWNER_CHAT_ID,
    FALLBACK_RESPONSE,
    CONTEXT_WINDOW_SIZE,
)

logger = logging.getLogger(__name__)

# Conversation states for appointment booking
BOOKING_SERVICE, BOOKING_DATE, BOOKING_TIME, BOOKING_CONFIRM = range(4)


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
        [KeyboardButton("📋 מחירון"), KeyboardButton("📅 קביעת תור")],
        [KeyboardButton("📍 שליחת מיקום"), KeyboardButton("👤 דברו עם נציג")],
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
    if t.startswith("אין לי את המידע הזה כרגע"):
        return True
    return False


async def _create_request_and_notify_owner(
    update: Update,
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
        update,
        context,
        user_id=user_id,
        display_name=display_name,
        telegram_username=telegram_username,
        message=reason,
    )

    response_text = (
        "אין לי את המידע הזה כרגע. תנו לי להעביר אתכם לנציג אנושי שיוכל לעזור. "
        "נציג אנושי יחזור אליכם בקרוב!"
    )
    db.save_message(user_id, display_name, "assistant", response_text)
    await update.message.reply_text(
        response_text,
        reply_markup=_get_main_keyboard(),
    )


# ─── /start Command ──────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command — send welcome message with menu."""
    user_id, display_name, _telegram_username = _get_user_info(update)
    
    welcome_text = (
        f"👋 ברוכים הבאים ל-*{BUSINESS_NAME}*!\n\n"
        f"אני העוזר הווירטואלי שלכם. אני יכול לעזור לכם עם:\n"
        f"• מידע על השירותים והמחירים שלנו\n"
        f"• קביעת תורים\n"
        f"• מענה על שאלות\n"
        f"• חיבור לנציג אנושי\n\n"
        f"פשוט כתבו את השאלה שלכם או השתמשו בכפתורים למטה! 👇"
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

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /help command."""
    help_text = (
        "🤖 *איך להשתמש בבוט:*\n\n"
        "• פשוט כתבו כל שאלה ואעשה כמיטב יכולתי לענות!\n"
        "• לחצו על *📋 מחירון* כדי לראות את השירותים והמחירים\n"
        "• לחצו על *📅 קביעת תור* כדי לקבוע ביקור\n"
        "• לחצו על *📍 שליחת מיקום* כדי לקבל את הכתובת והמפה שלנו\n"
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

async def price_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Price List button — retrieve pricing info from KB."""
    user_id, display_name, telegram_username = _get_user_info(update)
    
    await update.message.reply_text("📋 תנו לי רגע לחפש את המחירון שלנו...")
    
    # Use the RAG pipeline to find pricing information
    result = await _generate_answer_async("Show me the complete price list with all services and prices")
    
    db.save_message(user_id, display_name, "user", "📋 Price List")
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

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Send Location button — send business location info."""
    user_id, display_name, telegram_username = _get_user_info(update)
    
    # Use RAG to find location/address info
    result = await _generate_answer_async("What is the business address and location? How do I get there?")
    
    db.save_message(user_id, display_name, "user", "📍 Send Location")

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


# ─── Talk to Agent Button ────────────────────────────────────────────────────

async def talk_to_agent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Talk to Agent button — notify the business owner."""
    user_id, display_name, telegram_username = _get_user_info(update)
    
    # Create agent request in database
    await _create_request_and_notify_owner(
        update,
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
    
    db.save_message(user_id, display_name, "user", "👤 Talk to Agent")
    db.save_message(user_id, display_name, "assistant", response_text)
    
    await update.message.reply_text(
        response_text,
        reply_markup=_get_main_keyboard()
    )


# ─── Appointment Booking Flow ────────────────────────────────────────────────

async def booking_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the appointment booking conversation."""
    user_id, display_name, telegram_username = _get_user_info(update)
    
    # Get available services from KB
    result = await _generate_answer_async("What services do you offer? List them briefly.")

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
        "📅 *קביעת תור*\n\n"
        f"{stripped}\n\n"
        "אנא כתבו את *השירות* שתרצו להזמין "
        "(או הקלידו /cancel כדי לחזור):"
    )
    
    db.save_message(user_id, display_name, "user", "📅 Book Appointment")
    
    await _reply_markdown_safe(update.message, text)
    return BOOKING_SERVICE


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


async def booking_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the preferred time and show confirmation."""
    context.user_data["booking_time"] = update.message.text
    
    service = context.user_data.get("booking_service", "")
    date = context.user_data.get("booking_date", "")
    time = context.user_data.get("booking_time", "")
    
    confirmation_text = (
        "📋 *סיכום התור:*\n\n"
        f"• שירות: {service}\n"
        f"• תאריך: {date}\n"
        f"• שעה: {time}\n\n"
        "אנא אשרו על ידי כתיבת *כן* או *לא*:"
    )
    
    await update.message.reply_text(confirmation_text, parse_mode="Markdown")
    return BOOKING_CONFIRM


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
                    f"📅 בקשת תור חדשה #{appt_id}\n\n"
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
                        f"תור נקבע: {service} בתאריך {date} בשעה {time}")
        
        await update.message.reply_text(
            f"✅ התור שלכם נקבע!\n\n"
            f"• שירות: {service}\n"
            f"• תאריך: {date}\n"
            f"• שעה: {time}\n\n"
            f"נאשר את התור שלכם בקרוב. "
            f"תקבלו הודעה ברגע שהתור יאושר.",
            reply_markup=_get_main_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ התור בוטל. אין בעיה!\n"
            "אתם מוזמנים לקבוע תור חדש בכל עת.",
            reply_markup=_get_main_keyboard()
        )
    
    context.user_data.clear()
    return ConversationHandler.END


async def booking_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the booking flow."""
    context.user_data.clear()
    await update.message.reply_text(
        "ההזמנה בוטלה. איך עוד אפשר לעזור לכם?",
        reply_markup=_get_main_keyboard()
    )
    return ConversationHandler.END


# ─── Free-Text Message Handler ───────────────────────────────────────────────

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle any free-text message from the user.
    Routes through the RAG + LLM pipeline.
    """
    user_id, display_name, telegram_username = _get_user_info(update)
    user_message = update.message.text
    
    # Check for button texts and route accordingly
    if user_message == "📋 מחירון":
        return await price_list_handler(update, context)
    elif user_message == "📍 שליחת מיקום":
        return await location_handler(update, context)
    elif user_message == "👤 דברו עם נציג":
        return await talk_to_agent_handler(update, context)
    
    # Show typing indicator
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Get conversation history for context continuity
    history = db.get_conversation_history(user_id, limit=CONTEXT_WINDOW_SIZE)

    # Save user message
    db.save_message(user_id, display_name, "user", user_message)

    # Generate answer via RAG + LLM (with user_id for summary loading)
    result = await _generate_answer_async(
        user_query=user_message,
        conversation_history=history,
        user_id=user_id,
    )

    stripped = strip_source_citation(result["answer"])
    if _should_handoff_to_human(stripped):
        await _handoff_to_human(
            update,
            context,
            user_id=user_id,
            display_name=display_name,
            telegram_username=telegram_username,
            reason=f"הלקוח ביקש עזרה בנושא: {user_message}",
        )
    else:
        # Save assistant response (raw, with citation) for history consistency
        db.save_message(user_id, display_name, "assistant", result["answer"], ", ".join(result["sources"]))

        # Send citation-stripped response to customer
        await _reply_markdown_safe(
            update.message,
            stripped,
            reply_markup=_get_main_keyboard(),
        )

    # Trigger summarization in background (fire-and-forget, after response is sent).
    # context.application.create_task keeps a strong reference so the task
    # is not garbage-collected mid-execution.
    context.application.create_task(_summarize_safe(user_id))


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
