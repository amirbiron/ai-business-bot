"""
Configuration module for the AI Business Chatbot.
Loads settings from environment variables with sensible defaults.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

# ─── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
# Render-friendly storage configuration:
# - Render provides a dynamic `PORT` env var for web services.
# - For persistence you can mount a disk and set `DATA_DIR` to the mount path.
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "chatbot.db"))).resolve()
FAISS_INDEX_PATH = Path(os.getenv("FAISS_INDEX_PATH", str(DATA_DIR / "faiss_index"))).resolve()

# Ensure data directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
FAISS_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)

# ─── Telegram ────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_OWNER_CHAT_ID = os.getenv("TELEGRAM_OWNER_CHAT_ID", "")

# ─── OpenAI / LLM ───────────────────────────────────────────────────────────
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

# ─── RAG Settings ────────────────────────────────────────────────────────────
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "10"))
RAG_MIN_RELEVANCE = float(os.getenv("RAG_MIN_RELEVANCE", "0.3"))
CHUNK_MAX_TOKENS = int(os.getenv("CHUNK_MAX_TOKENS", "300"))

# ─── Admin Panel ─────────────────────────────────────────────────────────────
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme123")
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "super-secret-key-change-me")
ADMIN_HOST = os.getenv("ADMIN_HOST", "0.0.0.0")
ADMIN_PORT = int(os.getenv("ADMIN_PORT") or os.getenv("PORT") or "5000")

# ─── Business Info (defaults for demo) ───────────────────────────────────────
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Dana's Beauty Salon")

# ─── System Prompt (Layer A) ────────────────────────────────────────────────
SYSTEM_PROMPT = f"""You are a friendly and professional customer service representative for {BUSINESS_NAME}.

RULES — follow these strictly:
1. ONLY answer based on the provided context information. NEVER make up information.
2. If the context does not contain enough information to answer, say: "I don't have that information right now. Let me transfer you to a human agent who can help."
3. Always cite your source at the end of your answer using the format: Source: [category name or document title]
4. Be warm, helpful, and concise. Use a conversational tone.
5. If the customer wants to book an appointment, guide them to use the booking button.
6. If the customer asks about location, suggest using the location button.
7. If the customer seems frustrated or asks to speak to a person, suggest the "Talk to Agent" button.
8. Suggest relevant next actions when appropriate (e.g., "Would you like to book an appointment?").
9. Keep answers focused and under 200 words unless more detail is specifically requested.
10. Respond in the same language the customer uses."""

# ─── Quality Check (Layer C) ────────────────────────────────────────────────
SOURCE_CITATION_PATTERN = r"[Ss]ource:\s*.+"
FALLBACK_RESPONSE = (
    "I don't have that specific information right now. "
    "Let me connect you with a human agent who can help you better. "
    "Please tap the 'Talk to Agent' button below."
)
