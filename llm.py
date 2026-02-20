"""
LLM Module — Integrates the three-layer architecture:

  Layer A (System/Behavior): System prompt with behavior rules.
  Layer B (Context/RAG):     Retrieved context chunks injected into the prompt.
  Layer C (Quality Check):   Regex-based source citation verification.
"""

import re
import logging
from ai_chatbot.openai_client import get_openai_client

from ai_chatbot.config import (
    OPENAI_MODEL,
    LLM_MAX_TOKENS,
    SYSTEM_PROMPT,
    SOURCE_CITATION_PATTERN,
    FALLBACK_RESPONSE,
)
from ai_chatbot.rag.engine import retrieve, format_context

logger = logging.getLogger(__name__)


def _build_messages(
    user_query: str,
    context: str,
    conversation_history: list[dict] = None,
) -> list[dict]:
    """
    Build the messages array for the OpenAI Chat API.
    
    Layer A: System prompt with behavior rules.
    Layer B: Retrieved context injected as a system-level context message.
    Conversation history: Recent messages for continuity.
    User query: The current question.
    """
    messages = []
    
    # Layer A — System prompt
    messages.append({
        "role": "system",
        "content": SYSTEM_PROMPT
    })
    
    # Layer B — RAG context
    context_message = (
        "מידע הקשר (השתמש רק במידע זה כדי לענות על שאלת הלקוח):\n\n"
        f"{context}\n\n"
        "חשוב: בסס את תשובתך רק על המידע למעלה. "
        "תמיד סיים את התשובה עם 'מקור: [שם המקור]' בציון ההקשר שבו השתמשת."
    )
    messages.append({
        "role": "system",
        "content": context_message
    })
    
    # Conversation history (last N messages for continuity)
    if conversation_history:
        for msg in conversation_history[-10:]:  # Keep last 10 messages
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


def _strip_source_citation(response_text: str) -> str:
    """
    Remove source citation lines from the response before sending to the customer.

    The source citation (e.g. "מקור: מחירון קיץ 2025") is required internally
    for quality validation but should not be visible to end users.
    """
    cleaned = re.sub(r"\n*([Ss]ource|מקור):\s*.+", "", response_text)
    return cleaned.strip()


def generate_answer(
    user_query: str,
    conversation_history: list[dict] = None,
    top_k: int = None,
) -> dict:
    """
    Generate an answer for a user query using the full RAG pipeline.
    
    Steps:
    1. Retrieve relevant chunks (Layer B).
    2. Build prompt with system rules (Layer A) + context (Layer B).
    3. Call the LLM.
    4. Quality check the response (Layer C).
    
    Args:
        user_query: The customer's question.
        conversation_history: Previous messages for context continuity.
        top_k: Number of chunks to retrieve.
    
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
    
    # Step 2: Build messages (Layer A + B)
    messages = _build_messages(user_query, context, conversation_history)
    
    # Step 3: Call the LLM
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
        }
    
    # Step 4: Quality check (Layer C)
    final_answer = _quality_check(raw_answer)

    # Step 5: Strip source citation from customer-facing response
    final_answer = _strip_source_citation(final_answer)

    return {
        "answer": final_answer,
        "sources": sources,
        "chunks_used": len(chunks),
    }
