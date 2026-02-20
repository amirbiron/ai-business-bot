"""
Backward-compatible entrypoint.

Render and older docs run `python -m main`, so this module must execute the real
runner in `ai_chatbot.main`.
"""

from ai_chatbot.main import main


if __name__ == "__main__":
    main()
