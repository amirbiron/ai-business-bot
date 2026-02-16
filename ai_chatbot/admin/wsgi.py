"""
WSGI entrypoint for production servers (e.g. gunicorn).

Render can run this with:
  gunicorn ai_chatbot.admin.wsgi:app --bind 0.0.0.0:$PORT
"""

from ai_chatbot.admin.app import create_admin_app

app = create_admin_app()

