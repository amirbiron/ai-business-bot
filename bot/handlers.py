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
from ai_chatbot.llm import generate_answer
from ai_chatbot.config import (
    BUSINESS_NAME,
    TELEGRAM_OWNER_CHAT_ID,
    FALLBACK_RESPONSE,
)

logger = logging.getLogger(__name__)

# Conversation states for appointment booking
BOOKING_SERVICE, BOOKING_DATE, BOOKING_TIME, BOOKING_CONFIRM = range(4)


async def _generate_answer_async(*args, **kwargs):
    return await asyncio.to_thread(generate_answer, *args, **kwargs)


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


def _get_user_info(update: Update) -> tuple[str, str]:
    """Extract user ID and display name from an update."""
    user = update.effective_user
    user_id = str(user.id)
    username = user.full_name or user.username or f"User {user.id}"
    return user_id, username


# ─── /start Command ──────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command — send welcome message with menu."""
    user_id, username = _get_user_info(update)
    
    welcome_text = (
        f"👋 ברוכים הבאים ל-*{BUSINESS_NAME}*!\n\n"
        f"אני העוזר/ת הווירטואלי/ת שלכם. אני יכול/ה לעזור לכם עם:\n"
        f"• מידע על השירותים והמחירים שלנו\n"
        f"• קביעת תורים\n"
        f"• מענה על שאלות\n"
        f"• חיבור לנציג/ת אנושי/ת\n\n"
        f"פשוט כתבו את השאלה שלכם או השתמשו בכפתורים למטה! 👇"
    )
    
    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=_get_main_keyboard()
    )
    
    # Log the interaction
    db.save_message(user_id, username, "user", "/start")
    db.save_message(user_id, username, "assistant", "[Welcome message sent]")


# ─── /help Command ───────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /help command."""
    help_text = (
        "🤖 *איך להשתמש בבוט:*\n\n"
        "• פשוט כתבו כל שאלה ואעשה כמיטב יכולתי לענות!\n"
        "• לחצו על *📋 מחירון* כדי לראות את השירותים והמחירים\n"
        "• לחצו על *📅 קביעת תור* כדי לקבוע ביקור\n"
        "• לחצו על *📍 שליחת מיקום* כדי לקבל את הכתובת והמפה שלנו\n"
        "• לחצו על *👤 דברו עם נציג* כדי לדבר עם נציג/ה אמיתי/ת\n\n"
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
    user_id, username = _get_user_info(update)
    
    await update.message.reply_text("📋 תנו לי רגע לחפש את המחירון שלנו...")
    
    # Use the RAG pipeline to find pricing information
    result = await _generate_answer_async("Show me the complete price list with all services and prices")
    
    db.save_message(user_id, username, "user", "📋 Price List")
    db.save_message(user_id, username, "assistant", result["answer"], ", ".join(result["sources"]))
    
    await _reply_markdown_safe(
        update.message,
        result["answer"],
        reply_markup=_get_main_keyboard(),
    )


# ─── Send Location Button ────────────────────────────────────────────────────

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Send Location button — send business location info."""
    user_id, username = _get_user_info(update)
    
    # Use RAG to find location/address info
    result = await _generate_answer_async("What is the business address and location? How do I get there?")
    
    db.save_message(user_id, username, "user", "📍 Send Location")
    db.save_message(user_id, username, "assistant", result["answer"], ", ".join(result["sources"]))
    
    await _reply_markdown_safe(
        update.message,
        result["answer"],
        reply_markup=_get_main_keyboard(),
    )


# ─── Talk to Agent Button ────────────────────────────────────────────────────

async def talk_to_agent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Talk to Agent button — notify the business owner."""
    user_id, username = _get_user_info(update)
    
    # Create agent request in database
    request_id = db.create_agent_request(user_id, username, "לקוח/ה ביקש/ה לדבר עם נציג/ה")
    
    # Notify the business owner via Telegram
    if TELEGRAM_OWNER_CHAT_ID:
        try:
            notification = (
                f"🔔 *בקשת נציג #{request_id}*\n\n"
                f"לקוח/ה: {username}\n"
                f"מזהה משתמש: {user_id}\n"
                f"זמן: עכשיו\n\n"
                f"הלקוח/ה מבקש/ת לדבר עם נציג/ה אנושי/ת."
            )
            await context.bot.send_message(
                chat_id=TELEGRAM_OWNER_CHAT_ID,
                text=notification,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error("Failed to send owner notification: %s", e)
    
    response_text = (
        "👤 הודעתי לצוות שלנו שאתם מעוניינים לדבר עם מישהו.\n\n"
        "נציג/ה אנושי/ת יחזור/תחזור אליכם בקרוב. "
        "בינתיים, אתם מוזמנים לשאול אותי כל שאלה נוספת!"
    )
    
    db.save_message(user_id, username, "user", "👤 Talk to Agent")
    db.save_message(user_id, username, "assistant", response_text)
    
    await update.message.reply_text(
        response_text,
        reply_markup=_get_main_keyboard()
    )


# ─── Appointment Booking Flow ────────────────────────────────────────────────

async def booking_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the appointment booking conversation."""
    user_id, username = _get_user_info(update)
    
    # Get available services from KB
    result = await _generate_answer_async("What services do you offer? List them briefly.")
    
    text = (
        "📅 *קביעת תור*\n\n"
        f"{result['answer']}\n\n"
        "אנא כתבו את *השירות* שתרצו להזמין "
        "(או הקלידו /cancel כדי לחזור):"
    )
    
    db.save_message(user_id, username, "user", "📅 Book Appointment")
    
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
    user_id, username = _get_user_info(update)
    answer = update.message.text.lower().strip()
    
    if answer in ("yes", "y", "confirm", "כן", "אישור"):
        service = context.user_data.get("booking_service", "")
        date = context.user_data.get("booking_date", "")
        time = context.user_data.get("booking_time", "")
        
        # Save appointment to database
        appt_id = db.create_appointment(
            user_id=user_id,
            username=username,
            service=service,
            preferred_date=date,
            preferred_time=time,
        )
        
        # Notify business owner
        if TELEGRAM_OWNER_CHAT_ID:
            try:
                notification = (
                    f"📅 *בקשת תור חדשה #{appt_id}*\n\n"
                    f"לקוח/ה: {username}\n"
                    f"שירות: {service}\n"
                    f"תאריך: {date}\n"
                    f"שעה: {time}\n"
                )
                await context.bot.send_message(
                    chat_id=TELEGRAM_OWNER_CHAT_ID,
                    text=notification,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error("Failed to send appointment notification: %s", e)
        
        db.save_message(user_id, username, "assistant",
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
    user_id, username = _get_user_info(update)
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
    history = db.get_conversation_history(user_id, limit=10)
    
    # Save user message
    db.save_message(user_id, username, "user", user_message)
    
    # Generate answer via RAG + LLM
    result = await _generate_answer_async(
        user_query=user_message,
        conversation_history=history,
    )
    
    # Save assistant response
    db.save_message(user_id, username, "assistant", result["answer"], ", ".join(result["sources"]))
    
    # Send response
    await _reply_markdown_safe(
        update.message,
        result["answer"],
        reply_markup=_get_main_keyboard(),
    )


# ─── Error Handler ───────────────────────────────────────────────────────────

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors gracefully."""
    logger.error("Update %s caused error: %s", update, context.error)
    
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "מצטערים, משהו השתבש. אנא נסו שוב או לחצו על "
            "'👤 דברו עם נציג' כדי לדבר עם נציג/ה אנושי/ת.",
            reply_markup=_get_main_keyboard()
        )
