"""
LLM Module — Integrates the three-layer architecture:

  Layer A (System/Behavior): System prompt with behavior rules.
  Layer B (Context/RAG):     Retrieved context chunks injected into the prompt.
  Layer C (Quality Check):   Regex-based source citation verification.
"""

import re
import logging
import threading
from ai_chatbot.openai_client import get_openai_client

from ai_chatbot.config import (
    OPENAI_MODEL,
    LLM_MAX_TOKENS,
    SOURCE_CITATION_PATTERN,
    FALLBACK_RESPONSE,
    CONTEXT_WINDOW_SIZE,
    SUMMARY_THRESHOLD,
    FOLLOW_UP_ENABLED,
    build_system_prompt,
)
from ai_chatbot.rag.engine import retrieve, format_context
from ai_chatbot import database as db
from ai_chatbot.business_hours import get_hours_context_for_llm

logger = logging.getLogger(__name__)

# Per-user locks to prevent concurrent summarizations for the same user.
# Bounded to _MAX_LOCKS entries; oldest unlocked entries are evicted when full.
_MAX_LOCKS = 1000
_summarize_locks: dict[str, threading.Lock] = {}
_summarize_locks_guard = threading.Lock()


def _build_messages(
    user_query: str,
    context: str,
    conversation_history: list[dict] = None,
    conversation_summary: str = None,
) -> list[dict]:
    """
    Build the messages array for the OpenAI Chat API.

    Layer A: System prompt with behavior rules.
    Layer B: Retrieved context injected as a system-level context message.
    Conversation summary: Condensed history of older messages.
    Conversation history: Recent messages for continuity.
    User query: The current question.
    """
    messages = []

    # Layer A — System prompt דינמי (טון + DNA מה-DB, + הוראות שאלות המשך אם הפיצ'ר פעיל)
    try:
        settings = db.get_bot_settings()
        system_content = build_system_prompt(
            tone=settings.get("tone", "friendly"),
            custom_phrases=settings.get("custom_phrases", ""),
            follow_up_enabled=FOLLOW_UP_ENABLED,
        )
    except Exception as e:
        # fallback לפרומפט משופר עם ברירות מחדל (ללא תלות ב-DB)
        logger.error("Failed to load bot settings, using default prompt: %s", e)
        system_content = build_system_prompt(follow_up_enabled=FOLLOW_UP_ENABLED)
    messages.append({
        "role": "system",
        "content": system_content
    })

    # Layer B — RAG context + business hours context
    # Build hours context first so it can be included in the combined message
    hours_section = ""
    try:
        hours_context = get_hours_context_for_llm()
        hours_section = (
            "\n\nמידע שעות פעילות (מעודכן בזמן אמת):\n\n"
            f"{hours_context}"
        )
    except Exception as e:
        logger.error("Failed to build business hours context: %s", e)

    context_message = (
        "מידע הקשר:\n\n"
        f"{context}"
        f"{hours_section}\n\n"
        "חשוב: בסס את תשובתך רק על המידע למעלה (כולל מידע הקשר ושעות הפעילות). "
        "תמיד סיים את התשובה עם 'מקור: [שם המקור]' בציון ההקשר שבו השתמשת."
    )
    messages.append({
        "role": "system",
        "content": context_message
    })

    # Conversation summary (condensed older messages)
    if conversation_summary:
        messages.append({
            "role": "system",
            "content": (
                "סיכום השיחה הקודמת עם הלקוח (להמשכיות שיחה בלבד — "
                "אל תשתמש בסיכום זה כמקור לעובדות עסקיות כמו מחירים או שעות פתיחה; "
                "עובדות עסקיות מגיעות רק ממידע ההקשר למעלה):\n\n"
                f"{conversation_summary}"
            )
        })

    # Recent conversation history (last CONTEXT_WINDOW_SIZE messages for continuity)
    if conversation_history and CONTEXT_WINDOW_SIZE > 0:
        for msg in conversation_history[-CONTEXT_WINDOW_SIZE:]:
            messages.append({
                "role": msg["role"],
                "content": msg["message"]
            })

    # Current user query
    messages.append({
        "role": "user",
        "content": user_query
    })

    return messages


def _quality_check(response_text: str) -> str:
    """
    Layer C — Quality check using regex.
    
    Verifies that the LLM response contains a source citation.
    If no citation is found, returns the fallback safe response.
    
    Args:
        response_text: The raw LLM response.
    
    Returns:
        The response if it passes quality check, or the fallback response.
    """
    if re.search(SOURCE_CITATION_PATTERN, response_text):
        return response_text

    logger.warning(
        "Quality check failed — no source citation found. Response preview: '%s...'",
        response_text[:100],
    )
    return FALLBACK_RESPONSE


# ביטויים רגולריים לזיהוי שאלות המשך מתשובת ה-LLM
# תבנית ראשית: [שאלות_המשך: שאלה1 | שאלה2 | שאלה3]
# תבנית חלופית: שאלות המשך (עם/בלי קו תחתון, עם/בלי סוגריים מרובעים)
_FOLLOW_UP_PATTERN = re.compile(
    r"\[שאלות[_ ]המשך:\s*(.+?)\]"
)
_FOLLOW_UP_PATTERN_ALT = re.compile(
    r"שאלות[_ ]המשך:\s*(.+?)(?:\n|$)"
)


def extract_follow_up_questions(response_text: str) -> list[str]:
    """
    חילוץ שאלות המשך מתשובת ה-LLM.

    מחפש את התבנית [שאלות_המשך: שאלה1 | שאלה2 | שאלה3] ומחזיר רשימת שאלות.
    תומך גם בווריאציות נפוצות (בלי סוגריים, עם רווח במקום קו תחתון).
    מחזיר רשימה ריקה אם לא נמצאו שאלות.
    """
    match = _FOLLOW_UP_PATTERN.search(response_text)
    if not match:
        # ניסיון עם תבנית חלופית (בלי סוגריים מרובעים)
        match = _FOLLOW_UP_PATTERN_ALT.search(response_text)
        if match:
            logger.debug("follow-up: matched alt pattern (no brackets)")
    if not match:
        # לוג לדיבוג — מראה את סוף התשובה כדי להבין למה לא תפס
        tail = response_text[-200:] if len(response_text) > 200 else response_text
        logger.warning("follow-up: no match in response tail: %r", tail)
        return []
    raw = match.group(1)
    questions = [q.strip() for q in raw.split("|") if q.strip()]
    # הגבלה ל-3 שאלות מקסימום
    logger.debug("follow-up: extracted %d questions: %s", len(questions[:3]), questions[:3])
    return questions[:3]


def strip_follow_up_questions(response_text: str) -> str:
    """הסרת בלוק שאלות ההמשך (כולל שורות ריקות שלפניו) מהטקסט לפני שליחה ללקוח."""
    # הסרת הפורמט עם סוגריים מרובעים
    text = re.sub(r"\n*\[שאלות[_ ]המשך:\s*.*?\]", "", response_text)
    # הסרת הפורמט החלופי בלי סוגריים
    text = re.sub(r"\n*שאלות[_ ]המשך:\s*.+?(?:\n|$)", "\n", text)
    return text.strip()


def strip_source_citation(response_text: str) -> str:
    """
    Remove source citation lines from the response before sending to the customer.

    The source citation (e.g. "מקור: מחירון קיץ 2025") is required internally
    for quality validation but should not be visible to end users.
    """
    cleaned = re.sub(r"\n*" + SOURCE_CITATION_PATTERN, "", response_text)
    return cleaned.strip()


def _generate_summary(messages: list[dict], existing_summary: str = None) -> str | None:
    """
    Generate a concise summary of conversation messages using the LLM.

    If an existing summary is provided, it is merged with the new messages
    to create a single updated summary (recursive summarization).

    Args:
        messages: List of message dicts with 'role' and 'message' keys.
        existing_summary: Optional previous summary to merge with.

    Returns:
        A concise summary string, or None if generation failed.
    """
    conversation_text = "\n".join(
        f"{'לקוח' if m['role'] == 'user' else 'נציג'}: {m['message']}"
        for m in messages
    )

    prompt_parts = []
    if existing_summary:
        prompt_parts.append(f"סיכום קודם של השיחה:\n{existing_summary}\n")
    prompt_parts.append(f"הודעות חדשות:\n{conversation_text}")

    summary_prompt = (
        "אתה עוזר שמסכם שיחות שירות לקוחות.\n"
        "צור סיכום תמציתי של השיחה שלהלן. שמור על הנקודות העיקריות:\n"
        "- מה הלקוח שאל או ביקש\n"
        "- מה היו התשובות העיקריות\n"
        "- החלטות או פעולות שנעשו\n"
        "- העדפות או מידע חשוב על הלקוח\n\n"
        "חשוב: אל תכלול עובדות עסקיות (כמו מחירים, שעות פתיחה, כתובת). "
        "התמקד רק בהעדפות הלקוח, בקשותיו, והמשכיות השיחה.\n\n"
        + "\n".join(prompt_parts)
        + "\n\nסיכום:"
    )

    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": summary_prompt}],
            temperature=0.3,
            max_tokens=500,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error("Summary generation failed: %s", e)
        return None


def _get_user_lock(user_id: str) -> threading.Lock:
    """Get or create a per-user lock for summarization.

    Evicts the oldest unlocked entries when the dict exceeds _MAX_LOCKS.
    """
    with _summarize_locks_guard:
        if user_id not in _summarize_locks:
            # Evict stale unlocked entries if we've hit the cap
            if len(_summarize_locks) >= _MAX_LOCKS:
                to_remove = [
                    uid for uid, lock in _summarize_locks.items()
                    if not lock.locked()
                ]
                for uid in to_remove[:len(_summarize_locks) - _MAX_LOCKS + 1]:
                    del _summarize_locks[uid]
            _summarize_locks[user_id] = threading.Lock()
        return _summarize_locks[user_id]


def maybe_summarize(user_id: str):
    """
    Check if summarization is needed for a user and create a summary if so.

    Summarization is triggered when the number of unsummarized messages
    reaches SUMMARY_THRESHOLD. The new summary replaces all prior summaries
    (recursive merge into a single row).

    Uses a per-user lock to prevent concurrent summarizations.
    """
    lock = _get_user_lock(user_id)
    if not lock.acquire(blocking=False):
        # Another summarization is already running for this user
        return

    try:
        unsummarized_count = db.get_unsummarized_message_count(user_id)

        if unsummarized_count < SUMMARY_THRESHOLD:
            return

        # Get the messages that need summarizing
        messages_to_summarize = db.get_messages_for_summarization(
            user_id, SUMMARY_THRESHOLD
        )

        if not messages_to_summarize:
            return

        # Get the latest summary to merge with (recursive summarization)
        latest = db.get_latest_summary(user_id)
        existing_summary = latest["summary_text"] if latest else None

        # Generate the new merged summary
        summary_text = _generate_summary(messages_to_summarize, existing_summary)

        if summary_text is None:
            # LLM failed — don't advance the offset, messages will be retried next time
            logger.warning(
                "Skipping summary save for user %s due to generation failure", user_id
            )
            return

        # Record the id of the newest message we just summarized as the
        # high-water mark so future queries start from the right place.
        last_msg_id = max(m["id"] for m in messages_to_summarize)
        db.save_conversation_summary(
            user_id, summary_text, len(messages_to_summarize),
            last_summarized_message_id=last_msg_id,
        )
        logger.info(
            "Created conversation summary for user %s (%d messages summarized)",
            user_id, len(messages_to_summarize),
        )
    finally:
        lock.release()


def _get_conversation_summary(user_id: str) -> str | None:
    """
    Get the conversation summary for a user.

    Returns the single merged summary, or None if no summary exists.
    """
    latest = db.get_latest_summary(user_id)
    if not latest:
        return None
    return latest["summary_text"]


def generate_answer(
    user_query: str,
    conversation_history: list[dict] = None,
    top_k: int = None,
    user_id: str = None,
    username: str = None,
) -> dict:
    """
    Generate an answer for a user query using the full RAG pipeline.

    Steps:
    1. Retrieve relevant chunks (Layer B).
    2. Load conversation summary if available.
    3. Build prompt with system rules (Layer A) + context (Layer B) + summary + history.
    4. Call the LLM.
    5. Quality check the response (Layer C).

    Args:
        user_query: The customer's question.
        conversation_history: Previous messages for context continuity.
        top_k: Number of chunks to retrieve.
        user_id: The user ID for loading conversation summaries.

    Returns:
        Dict with 'answer', 'sources', and 'chunks_used'.
    """
    # Step 1: Retrieve relevant context (Layer B)
    chunks = retrieve(user_query, top_k=top_k)
    context = format_context(chunks)

    # Collect source labels
    sources = list(set(
        f"{c['category']} — {c['title']}" for c in chunks
    ))

    # Step 2: Load conversation summary
    conversation_summary = None
    if user_id:
        conversation_summary = _get_conversation_summary(user_id)

    # Step 3: Build messages (Layer A + B + summary + history)
    messages = _build_messages(
        user_query, context, conversation_history, conversation_summary
    )

    # Step 4: Call the LLM
    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=0.3,
            max_tokens=LLM_MAX_TOKENS,
        )
        raw_answer = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error("LLM API error: %s", e)
        return {
            "answer": FALLBACK_RESPONSE,
            "sources": [],
            "chunks_used": 0,
            "follow_up_questions": [],
        }

    # חילוץ שאלות המשך לפני בדיקת איכות (הן לא חלק מהתשובה עצמה)
    follow_up_questions = []
    if FOLLOW_UP_ENABLED:
        follow_up_questions = extract_follow_up_questions(raw_answer)
        raw_answer = strip_follow_up_questions(raw_answer)
    else:
        logger.debug("follow-up: FOLLOW_UP_ENABLED is False, skipping extraction")

    # Step 5: Quality check (Layer C)
    final_answer = _quality_check(raw_answer)

    # Log unanswered question if fallback was triggered
    if final_answer == FALLBACK_RESPONSE and user_id:
        if follow_up_questions:
            logger.debug("follow-up: clearing %d questions due to fallback", len(follow_up_questions))
        follow_up_questions = []  # לא מציגים שאלות המשך על fallback
        try:
            db.save_unanswered_question(user_id, username or "", user_query)
        except Exception as e:
            logger.error("Failed to log unanswered question: %s", e)

    return {
        "answer": final_answer,
        "sources": sources,
        "chunks_used": len(chunks),
        "follow_up_questions": follow_up_questions,
    }
