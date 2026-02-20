"""
ai_chatbot package
------------------
This repository's source code currently lives at the repo root (e.g. `config.py`,
`database.py`, `admin/`, `bot/`, `rag/`). The application code imports modules
via the `ai_chatbot.*` namespace, so for deployments (including Render) we ship
this lightweight package wrapper that forwards imports to the existing modules.
"""

__version__ = "1.0.0"

