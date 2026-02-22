"""
Bot State — מודול משותף לשמירת רפרנסים לאובייקט הבוט ו-event loop.

כש-main.py מפעיל את הבוט, הוא מאחסן כאן את ה-Bot ואת ה-event loop.
פאנל האדמין (שרץ ב-thread נפרד) משתמש בהם לשליחת broadcast.
"""

import asyncio
from typing import Optional

from telegram import Bot

# מאותחל ע"י telegram_bot.py ב-post_init
_bot: Optional[Bot] = None
_loop: Optional[asyncio.AbstractEventLoop] = None


def set_bot(bot: Bot, loop: asyncio.AbstractEventLoop) -> None:
    """שמירת רפרנס לבוט ול-event loop (נקרא מ-post_init של הבוט)."""
    global _bot, _loop
    _bot = bot
    _loop = loop


def get_bot() -> Optional[Bot]:
    """קבלת אובייקט ה-Bot (או None אם הבוט לא פעיל)."""
    return _bot


def get_loop() -> Optional[asyncio.AbstractEventLoop]:
    """קבלת ה-event loop של הבוט (או None אם הבוט לא פעיל)."""
    return _loop
