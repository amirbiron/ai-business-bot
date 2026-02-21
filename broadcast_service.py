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
) -> None:
    """שליחת הודעת שידור לרשימת נמענים ברקע.

    מעדכן את ה-DB בהתקדמות ובסיום. מטפל ב-RetryAfter (429) ו-Forbidden (חסום).
    """
    sent = 0
    failed = 0

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


def start_broadcast_task(
    bot: Bot,
    broadcast_id: int,
    message_text: str,
    recipients: list[str],
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> None:
    """הפעלת שליחת שידור כ-task ברקע ב-event loop קיים.

    נקרא מתוך Flask (thread נפרד) — מזריק task ל-event loop של הבוט.
    אם אין event loop (למשל admin-only mode) — שולח סינכרוני ב-thread חדש.
    """
    if loop is not None and loop.is_running():
        asyncio.run_coroutine_threadsafe(
            send_broadcast(bot, broadcast_id, message_text, recipients),
            loop,
        )
    else:
        # fallback — הרצה בלולאה חדשה (admin-only mode ללא בוט פעיל)
        import threading

        def _run():
            try:
                asyncio.run(send_broadcast(bot, broadcast_id, message_text, recipients))
            except Exception as e:
                logger.error("Broadcast thread failed: %s", e)
                db.fail_broadcast(broadcast_id, 0, len(recipients))

        thread = threading.Thread(target=_run, daemon=True, name=f"broadcast-{broadcast_id}")
        thread.start()
