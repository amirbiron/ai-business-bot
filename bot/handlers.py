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
from ai_chatbot.intent import Intent, detect_intent, get_direct_response
from ai_chatbot.business_hours import is_currently_open, get_weekly_schedule_text
from ai_chatbot.config import (
    BUSINESS_NAME,
    TELEGRAM_OWNER_CHAT_ID,
    FALLBACK_RESPONSE,
    CONTEXT_WINDOW_SIZE,
)
from ai_chatbot.live_chat_service import live_chat_guard, live_chat_guard_booking
from ai_chatbot.rate_limiter import rate_limit_guard, rate_limit_guard_booking

logger = logging.getLogger(__name__)

# Conversation states for appointment booking
BOOKING_SERVICE, BOOKING_DATE, BOOKING_TIME, BOOKING_CONFIRM = range(4)

# Button label constants — used for routing and filtering
BUTTON_PRICE_LIST = "📋 מחירון"
BUTTON_BOOKING = "📅 קביעת תור"
BUTTON_LOCATION = "📍 שליחת מיקום"
BUTTON_AGENT = "👤 דברו עם נציג"
ALL_BUTTON_TEXTS = [BUTTON_PRICE_LIST, BUTTON_BOOKING, BUTTON_LOCATION, BUTTON_AGENT]


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
        [KeyboardButton(BUTTON_LOCATION), KeyboardButton(BUTTON_AGENT)],
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

@rate_limit_guard
@live_chat_guard
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /help command."""
    user_id, display_name, _ = _get_user_info(update)

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


# ─── Talk to Agent Button ────────────────────────────────────────────────────

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


# ─── Appointment Booking Flow ────────────────────────────────────────────────

@rate_limit_guard_booking
@live_chat_guard_booking
async def booking_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the appointment booking conversation."""
    user_id, display_name, telegram_username = _get_user_info(update)

    # Log the user's booking attempt even if we handoff to human.
    db.save_message(user_id, display_name, "user", "📅 קביעת תור")
    
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
        "📅 *קביעת תור*\n\n"
        f"{stripped}\n\n"
        "אנא כתבו את *השירות* שתרצו להזמין "
        "(או הקלידו /cancel כדי לחזור):"
    )
    
    await _reply_markdown_safe(update.message, text)
    return BOOKING_SERVICE


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
        "📋 *סיכום התור:*\n\n"
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


@rate_limit_guard_booking
@live_chat_guard_booking
async def booking_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the booking flow."""
    context.user_data.clear()
    await update.message.reply_text(
        "ההזמנה בוטלה. איך עוד אפשר לעזור לכם?",
        reply_markup=_get_main_keyboard()
    )
    return ConversationHandler.END


@rate_limit_guard_booking
@live_chat_guard_booking
async def booking_button_interrupt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle button clicks during an active booking — cancel booking and route to the clicked button."""
    context.user_data.clear()
    user_message = update.message.text

    # Use __wrapped__ to skip the rate_limit_guard layer — the current
    # handler already recorded the message.
    if user_message == BUTTON_BOOKING:
        # Restart the booking flow from scratch
        return await booking_start.__wrapped__(update, context)

    if user_message == BUTTON_PRICE_LIST:
        await price_list_handler.__wrapped__(update, context)
    elif user_message == BUTTON_LOCATION:
        await location_handler.__wrapped__(update, context)
    elif user_message == BUTTON_AGENT:
        await talk_to_agent_handler.__wrapped__(update, context)
    else:
        # Safety fallback — should not happen, but avoid a silent dead-end
        logger.warning("booking_button_interrupt: unexpected text %r", user_message)
        await update.message.reply_text(
            "ההזמנה בוטלה. איך עוד אפשר לעזור לכם?",
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

    # Check for button texts and route accordingly.
    # Use __wrapped__ to skip the rate_limit_guard layer — the current
    # handler already recorded the message so re-entering the decorated
    # version would count it twice.
    if user_message == BUTTON_PRICE_LIST:
        return await price_list_handler.__wrapped__(update, context)
    elif user_message == BUTTON_LOCATION:
        return await location_handler.__wrapped__(update, context)
    elif user_message == BUTTON_AGENT:
        return await talk_to_agent_handler.__wrapped__(update, context)

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
        response = (
            "אשמח לעזור לכם לקבוע תור! 📅\n\n"
            "לחצו על הכפתור *📅 קביעת תור* למטה כדי להתחיל."
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
