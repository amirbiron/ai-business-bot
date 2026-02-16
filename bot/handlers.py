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

import logging
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
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


def _get_main_keyboard() -> ReplyKeyboardMarkup:
    """Create the main menu keyboard with action buttons."""
    keyboard = [
        [KeyboardButton("📋 Price List"), KeyboardButton("📅 Book Appointment")],
        [KeyboardButton("📍 Send Location"), KeyboardButton("👤 Talk to Agent")],
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
        f"👋 Welcome to *{BUSINESS_NAME}*!\n\n"
        f"I'm your virtual assistant. I can help you with:\n"
        f"• Information about our services and prices\n"
        f"• Booking appointments\n"
        f"• Answering your questions\n"
        f"• Connecting you with a human agent\n\n"
        f"Just type your question or use the buttons below! 👇"
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
        "🤖 *How to use this bot:*\n\n"
        "• Just type any question and I'll do my best to answer!\n"
        "• Use *📋 Price List* to see our services and prices\n"
        "• Use *📅 Book Appointment* to schedule a visit\n"
        "• Use *📍 Send Location* to get our address and map\n"
        "• Use *👤 Talk to Agent* to speak with a real person\n\n"
        "You can also type questions like:\n"
        '  _"What are your opening hours?"_\n'
        '  _"Do you offer hair coloring?"_\n'
        '  _"What is your cancellation policy?"_'
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
    
    await update.message.reply_text("📋 Let me look up our price list for you...")
    
    # Use the RAG pipeline to find pricing information
    result = generate_answer("Show me the complete price list with all services and prices")
    
    db.save_message(user_id, username, "user", "📋 Price List")
    db.save_message(user_id, username, "assistant", result["answer"], ", ".join(result["sources"]))
    
    await update.message.reply_text(
        result["answer"],
        parse_mode="Markdown",
        reply_markup=_get_main_keyboard()
    )


# ─── Send Location Button ────────────────────────────────────────────────────

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Send Location button — send business location info."""
    user_id, username = _get_user_info(update)
    
    # Use RAG to find location/address info
    result = generate_answer("What is the business address and location? How do I get there?")
    
    db.save_message(user_id, username, "user", "📍 Send Location")
    db.save_message(user_id, username, "assistant", result["answer"], ", ".join(result["sources"]))
    
    await update.message.reply_text(
        result["answer"],
        parse_mode="Markdown",
        reply_markup=_get_main_keyboard()
    )


# ─── Talk to Agent Button ────────────────────────────────────────────────────

async def talk_to_agent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Talk to Agent button — notify the business owner."""
    user_id, username = _get_user_info(update)
    
    # Create agent request in database
    request_id = db.create_agent_request(user_id, username, "Customer requested to speak with an agent")
    
    # Notify the business owner via Telegram
    if TELEGRAM_OWNER_CHAT_ID:
        try:
            notification = (
                f"🔔 *Agent Request #{request_id}*\n\n"
                f"Customer: {username}\n"
                f"User ID: {user_id}\n"
                f"Time: Now\n\n"
                f"The customer wants to speak with a human agent."
            )
            await context.bot.send_message(
                chat_id=TELEGRAM_OWNER_CHAT_ID,
                text=notification,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send owner notification: {e}")
    
    response_text = (
        "👤 I've notified our team that you'd like to speak with someone.\n\n"
        "A human agent will get back to you shortly. "
        "In the meantime, feel free to ask me any other questions!"
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
    result = generate_answer("What services do you offer? List them briefly.")
    
    text = (
        "📅 *Book an Appointment*\n\n"
        f"{result['answer']}\n\n"
        "Please type the *service* you'd like to book "
        "(or type /cancel to go back):"
    )
    
    db.save_message(user_id, username, "user", "📅 Book Appointment")
    
    await update.message.reply_text(text, parse_mode="Markdown")
    return BOOKING_SERVICE


async def booking_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the service selection."""
    context.user_data["booking_service"] = update.message.text
    
    await update.message.reply_text(
        "📆 Great! What *date* would you prefer?\n"
        "(e.g., 'Monday', 'March 15', 'tomorrow')\n\n"
        "Type /cancel to go back.",
        parse_mode="Markdown"
    )
    return BOOKING_DATE


async def booking_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the preferred date."""
    context.user_data["booking_date"] = update.message.text
    
    await update.message.reply_text(
        "🕐 What *time* works best for you?\n"
        "(e.g., '10:00 AM', 'afternoon', '2pm')\n\n"
        "Type /cancel to go back.",
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
        "📋 *Appointment Summary:*\n\n"
        f"• Service: {service}\n"
        f"• Date: {date}\n"
        f"• Time: {time}\n\n"
        "Please confirm by typing *yes* or *no*:"
    )
    
    await update.message.reply_text(confirmation_text, parse_mode="Markdown")
    return BOOKING_CONFIRM


async def booking_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle booking confirmation."""
    user_id, username = _get_user_info(update)
    answer = update.message.text.lower().strip()
    
    if answer in ("yes", "y", "confirm", "כן"):
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
                    f"📅 *New Appointment Request #{appt_id}*\n\n"
                    f"Customer: {username}\n"
                    f"Service: {service}\n"
                    f"Date: {date}\n"
                    f"Time: {time}\n"
                )
                await context.bot.send_message(
                    chat_id=TELEGRAM_OWNER_CHAT_ID,
                    text=notification,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send appointment notification: {e}")
        
        db.save_message(user_id, username, "assistant",
                        f"Appointment booked: {service} on {date} at {time}")
        
        await update.message.reply_text(
            f"✅ Your appointment has been booked!\n\n"
            f"• Service: {service}\n"
            f"• Date: {date}\n"
            f"• Time: {time}\n\n"
            f"We'll confirm your appointment shortly. "
            f"You'll receive a notification once it's confirmed.",
            reply_markup=_get_main_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ Appointment cancelled. No worries!\n"
            "Feel free to book again anytime.",
            reply_markup=_get_main_keyboard()
        )
    
    context.user_data.clear()
    return ConversationHandler.END


async def booking_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the booking flow."""
    context.user_data.clear()
    await update.message.reply_text(
        "Booking cancelled. How else can I help you?",
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
    if user_message == "📋 Price List":
        return await price_list_handler(update, context)
    elif user_message == "📅 Book Appointment":
        # This should be caught by the ConversationHandler, but just in case
        return await booking_start(update, context)
    elif user_message == "📍 Send Location":
        return await location_handler(update, context)
    elif user_message == "👤 Talk to Agent":
        return await talk_to_agent_handler(update, context)
    
    # Show typing indicator
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    # Get conversation history for context continuity
    history = db.get_conversation_history(user_id, limit=10)
    
    # Save user message
    db.save_message(user_id, username, "user", user_message)
    
    # Generate answer via RAG + LLM
    result = generate_answer(
        user_query=user_message,
        conversation_history=history,
    )
    
    # Save assistant response
    db.save_message(user_id, username, "assistant", result["answer"], ", ".join(result["sources"]))
    
    # Send response
    await update.message.reply_text(
        result["answer"],
        parse_mode="Markdown",
        reply_markup=_get_main_keyboard()
    )


# ─── Error Handler ───────────────────────────────────────────────────────────

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors gracefully."""
    logger.error(f"Update {update} caused error: {context.error}")
    
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "I'm sorry, something went wrong. Please try again or tap "
            "'👤 Talk to Agent' to speak with a human.",
            reply_markup=_get_main_keyboard()
        )
