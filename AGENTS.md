# AGENTS.md

## Cursor Cloud specific instructions

### Overview

AI Business Chatbot — a Telegram bot + Flask admin panel for small businesses (demo: "Dana's Beauty Salon"). Single-process Python app, SQLite database, FAISS vector store, OpenAI LLM/embeddings with local hash-based fallback.

### Services

| Service | Command | Notes |
|---------|---------|-------|
| Admin panel only | `python3 main.py --admin` | Runs on `http://0.0.0.0:5000`, works without Telegram/OpenAI tokens |
| Bot only | `python3 main.py --bot` | Requires `TELEGRAM_BOT_TOKEN` |
| Both | `python3 main.py` | Admin in background thread, bot in main thread |
| Seed data | `python3 main.py --seed` | Populates KB + builds FAISS index (uses local fallback if no OpenAI key) |

### Key caveats

- Use `python3` not `python` — the system does not have a `python` alias.
- The `.env` file must exist (copy from `.env.example`). `ADMIN_PASSWORD` and `ADMIN_SECRET_KEY` are required for the admin panel to accept logins.
- Seeding and RAG index building work without `OPENAI_API_KEY` via a local hash-based embedding fallback (not semantically meaningful, but functional for testing).
- The Telegram bot will not start without `TELEGRAM_BOT_TOKEN`; the app gracefully falls back to admin-only mode.
- Auto-seed: if the KB is empty on startup, the app auto-seeds demo data — no manual `--seed` step required for first run.
- SQLite DB is stored at `data/chatbot.db`; FAISS index at `data/faiss_index/`. Both are created automatically.

### Environment secrets

| Secret | Purpose | Required for |
|--------|---------|-------------|
| `OPENAI_API_KEY` | LLM chat completions + real embeddings | Full RAG pipeline (fallback works without) |
| `TELEGRAM_BOT_TOKEN` | Telegram bot polling | Bot mode |
| `TELEGRAM_OWNER_CHAT_ID` | Notifications to business owner | Bot notifications |

These are injected as env vars. The `.env` file picks them up via `python-dotenv`.

### Lint, test, build

- See `CLAUDE.md` for canonical commands. Key ones:
  - **Lint:** `flake8 --max-line-length=120 .`
  - **Tests:** `python3 -m pytest tests/ -v` (213 tests, all mocked, no external APIs needed)

### Testing the RAG pipeline programmatically

When Telegram Web is not accessible, you can test the bot's core logic directly:

```python
from ai_chatbot.llm import generate_answer
result = generate_answer(user_query="כמה עולה תספורת?", user_id="test_user")
print(result["answer"])
```

This exercises the full RAG pipeline (embed query -> FAISS search -> LLM generation -> quality check).
