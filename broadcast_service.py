"""
BroadcastService — שירות לשליחת הודעות יזומות (broadcast) ללקוחות.

השירות מקבל הודעה ורשימת נמענים, ושולח ברקע עם delay בין הודעות
כדי לעמוד במגבלות Telegram (rate limit).

ארכיטקטורה:
- הפאנל יוצר broadcast_messages ומפעיל את ה-worker דרך asyncio.
- ה-worker שולח הודעה-הודעה עם השהייה, מעדכן את ה-DB בהתקדמות,
  ומטפל ב-RetryAfter / Forbidden בצורה גמישה.
"""

import asyncio
import logging
from typing import Optional

from telegram import Bot
from telegram.error import Forbidden, RetryAfter, TimedOut, BadRequest

from ai_chatbot import database as db

logger = logging.getLogger(__name__)

# השהייה בין הודעות — 0.05 שניות (20 הודעות בשנייה, מתחת למגבלת טלגרם)
_SEND_DELAY = 0.05

# עדכון התקדמות ב-DB כל N הודעות (לא כל הודעה — חוסך עומס על ה-DB)
_PROGRESS_UPDATE_INTERVAL = 10


async def send_broadcast(
    bot: Bot,
    broadcast_id: int,
    message_text: str,
    recipients: list[str],
    *,
    needs_init: bool = False,
) -> None:
    """שליחת הודעת שידור לרשימת נמענים ברקע.

    מעדכן את ה-DB בהתקדמות ובסיום. מטפל ב-RetryAfter (429) ו-Forbidden (חסום).
    אם needs_init=True (admin-only mode), מאתחל את ה-Bot לפני השליחה וסוגר בסוף.
    """
    # אתחול Bot שנוצר מחוץ ל-Application (admin-only mode)
    if needs_init:
        await bot.initialize()

    sent = 0
    failed = 0

    try:
        # סימון מיידי כ-sending — גם לרשימות קטנות מ-PROGRESS_UPDATE_INTERVAL
        db.mark_broadcast_sending(broadcast_id)

        for i, user_id in enumerate(recipients):
            try:
                await bot.send_message(chat_id=int(user_id), text=message_text)
                sent += 1
            except Forbidden:
                # המשתמש חסם את הבוט — מסמנים כלא-מנוי
                logger.info("Broadcast %d: user %s blocked the bot, unsubscribing", broadcast_id, user_id)
                db.unsubscribe_user(user_id)
                failed += 1
            except RetryAfter as e:
                # טלגרם מבקש להמתין — מכבדים ומנסים שוב
                logger.warning("Broadcast %d: rate limited, waiting %s seconds", broadcast_id, e.retry_after)
                await asyncio.sleep(e.retry_after)
                try:
                    await bot.send_message(chat_id=int(user_id), text=message_text)
                    sent += 1
                except Forbidden:
                    # המשתמש חסם את הבוט גם בניסיון החוזר — מסמנים כלא-מנוי
                    logger.info("Broadcast %d: user %s blocked the bot on retry, unsubscribing", broadcast_id, user_id)
                    db.unsubscribe_user(user_id)
                    failed += 1
                except Exception as retry_err:
                    logger.error("Broadcast %d: retry failed for user %s: %s", broadcast_id, user_id, retry_err)
                    failed += 1
            except (TimedOut, BadRequest) as e:
                logger.error("Broadcast %d: failed for user %s: %s", broadcast_id, user_id, e)
                failed += 1
            except Exception as e:
                logger.error("Broadcast %d: unexpected error for user %s: %s", broadcast_id, user_id, e)
                failed += 1

            # עדכון התקדמות ב-DB מדי פעם
            if (i + 1) % _PROGRESS_UPDATE_INTERVAL == 0:
                db.update_broadcast_progress(broadcast_id, sent, failed)

            await asyncio.sleep(_SEND_DELAY)

        # סיום — עדכון סופי
        db.complete_broadcast(broadcast_id, sent, failed)
        logger.info(
            "Broadcast %d completed: %d sent, %d failed out of %d recipients",
            broadcast_id, sent, failed, len(recipients),
        )
    finally:
        # סגירת ה-Bot אם אותחל כאן (admin-only mode)
        # בתוך try/except נפרד כדי שכשל ב-shutdown לא ידרוס סטטוס completed
        if needs_init:
            try:
                await bot.shutdown()
            except Exception as e:
                logger.error("Broadcast %d: bot shutdown failed: %s", broadcast_id, e)


def start_broadcast_task(
    bot: Bot,
    broadcast_id: int,
    message_text: str,
    recipients: list[str],
    loop: Optional[asyncio.AbstractEventLoop] = None,
    *,
    needs_init: bool = False,
) -> None:
    """הפעלת שליחת שידור כ-task ברקע ב-event loop קיים.

    נקרא מתוך Flask (thread נפרד) — מזריק task ל-event loop של הבוט.
    אם אין event loop (למשל admin-only mode) — שולח סינכרוני ב-thread חדש.
    """
    if loop is not None and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(
            send_broadcast(bot, broadcast_id, message_text, recipients, needs_init=needs_init),
            loop,
        )
        # טיפול בשגיאות שנופלות מחוץ ללולאת ה-per-message (למשל DB errors)
        future.add_done_callback(
            lambda f: _handle_future_error(f, broadcast_id)
        )
    else:
        # fallback — הרצה בלולאה חדשה (admin-only mode ללא בוט פעיל)
        import threading

        def _run():
            try:
                asyncio.run(send_broadcast(bot, broadcast_id, message_text, recipients, needs_init=needs_init))
            except Exception as e:
                logger.error("Broadcast thread failed: %s", e)
                # לא דורסים sent/failed — שומרים את ההתקדמות שכבר נכתבה ל-DB
                db.fail_broadcast(broadcast_id)

        thread = threading.Thread(target=_run, daemon=True, name=f"broadcast-{broadcast_id}")
        thread.start()


def _handle_future_error(future: asyncio.Future, broadcast_id: int) -> None:
    """callback לטיפול בשגיאות של broadcast task שרץ ב-event loop."""
    if future.cancelled():
        # ה-task בוטל (למשל כיבוי הבוט) — מסמנים ככישלון כדי שלא יישאר תקוע ב-sending
        logger.warning("Broadcast %d task was cancelled", broadcast_id)
        try:
            db.fail_broadcast(broadcast_id)
        except Exception as db_err:
            logger.error("Broadcast %d: failed to mark cancelled broadcast in DB: %s", broadcast_id, db_err)
        return

    exc = future.exception()
    if exc is not None:
        logger.error("Broadcast %d task failed: %s", broadcast_id, exc)
        try:
            # לא דורסים sent/failed — שומרים את ההתקדמות שכבר נכתבה ל-DB
            db.fail_broadcast(broadcast_id)
        except Exception as db_err:
            logger.error("Broadcast %d: failed to mark as failed in DB: %s", broadcast_id, db_err)
