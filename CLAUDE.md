# CLAUDE.md — הנחיות פיתוח לפרויקט ai-business-bot

## שפה

- סיכומי PR, תיאורי commit, והודעות סשן — **בעברית**
- הערות בקוד (comments) — **בעברית**
- שמות משתנים, פונקציות, וטבלאות — באנגלית (כמקובל)

## ארכיטקטורה

- **מבנה מודולים:** קוד המקור בשורש הריפו (`config.py`, `database.py`, וכו'). חבילת `ai_chatbot/` מכילה wrappers שמייצאים מהשורש. כשמוסיפים מודול חדש בשורש — ליצור גם wrapper ב-`ai_chatbot/`.
- **בסיס נתונים:** SQLite עם WAL mode. סכימה ב-`init_db()`. מיגרציות קלות (ADD COLUMN, אינדקסים) באותו הפונקציה.
- **Admin:** Flask + HTMX + Jinja2. RTL עברית. תבניות ב-`admin/templates/`.
- **בוט:** python-telegram-bot (async). Handlers ב-`bot/handlers.py`.
- **LLM:** שלוש שכבות — A (system prompt), B (RAG context), C (quality check עם regex).

## כללי פיתוח

### DB — אילוצים מהרגע הראשון
- לכל טבלה חדשה: לזהות מהו ה-natural key ולהוסיף `UNIQUE` constraint.
- אם יש seed data שמשתמש יכול לדרוס — להשתמש ב-`INSERT OR REPLACE` ולא `INSERT`.

### LLM Prompts — לקרוא כשלם
- כשמזריקים תוכן חדש ל-prompt — לקרוא את כל ההודעות יחד ולוודא שאין הוראות סותרות (למשל "השתמש **רק** במידע X" ואז מידע Y בהודעה נפרדת).

### HTMX — DOM consistency
- כש-HTMX מוחק/מחליף אלמנט, לוודא שכל האלמנטים הקשורים (כמו טופס עריכה מוסתר) נמחקים יחד. לעטוף קבוצות קשורות בקונטיינר משותף שה-target מכוון אליו.

### Routes — לא dead code
- לכל route חדש — לוודא שיש UI שקורא לו באותו commit. לא להוסיף endpoint בלי caller.

### לוגיקת זמן — טבלת תרחישים
- לפני כתיבת לוגיקה שתלויה בזמן/תאריך — לכתוב טבלת תרחישים עם כל מקרי הקצה (שעות לילה, מעבר יום, ערבי חג על ימים סגורים, גבולות שנה).

### Exceptions — תמיד לרשום ללוג
- `except Exception: pass` אסור. תמיד `logger.error(...)` כדי שבאגים לא ייעלמו בשקט.

### Handlers — דקורטורים על כל handler
- כל handler חדש (command, message, callback) חייב לעבור דרך `@rate_limit_guard` ו-`@live_chat_guard`. בלי `@live_chat_guard` — ה-handler יגיב ישירות למשתמש במהלך live chat ויפר את הזרימה.

### לולאות I/O ארוכות — עמידות בפני כשלים
- בלולאה שמבצעת I/O (רשת, DB) על רשימת פריטים: לעטוף **כל** קריאת I/O בתוך הלולאה ב-`try/except` עם לוג. כשל בפריט אחד לא צריך לעצור את עיבוד שאר הפריטים. דוגמה: `broadcast_service.py` — כשל DB בהודעה 10 לא עוצר 990 הודעות שנותרו.

### asyncio — ניהול lifecycle ו-futures
- **Bot standalone**: `Bot(token=...)` שנוצר מחוץ ל-`Application` דורש `await bot.initialize()` לפני שימוש ו-`await bot.shutdown()` בסיום (python-telegram-bot v20+).
- **Futures**: כש-`run_coroutine_threadsafe` מחזיר `Future` — לא לזרוק אותו. להוסיף `add_done_callback` שמטפל בכשלון. לבדוק `future.cancelled()` **לפני** `future.exception()`.
- **Cleanup ב-finally**: אם `shutdown()` / `close()` יכול להיכשל — לעטוף ב-`try/except` נפרד כדי שלא ידרוס את התוצאה של הפעולה העיקרית (למשל סטטוס `completed` שכבר נכתב ל-DB).

### DB — לא לדרוס התקדמות ב-error paths
- כשפונקציית כישלון (כמו `fail_broadcast`) נקראת ב-error handler — לא לדרוס מונים (sent/failed) עם 0 אם כבר נכתבה התקדמות ל-DB. לתמוך בקריאה ללא מונים שמעדכנת רק סטטוס.

### DB — למנוע כפילות לוגיקה בשאילתות
- כשיש שתי פונקציות שחולקות לוגיקת סינון (למשל `get_X` ו-`count_X`) — לחלץ helper פנימי משותף. שכפול WHERE/JOIN בין פונקציות מזמין סטייה שקטה כשמעדכנים רק אחת מהן.

### Handlers — צינור RAG אחד בלבד
- כל נתיב שמפעיל את צינור ה-RAG (כולל callback queries) חייב לעבור דרך `_handle_rag_query` ולא לשכפל את הלוגיקה. לצורך callbacks בלי `update.message` — להעביר `chat_id`.

### Handlers — rate limit על כל קריאת LLM
- כל נתיב שמגיע ל-LLM (הודעות, callbacks, שאלות המשך) חייב לעבור בדיקת `check_rate_limit` + `record_message`. ללא זה משתמש יכול לעקוף את מגבלות הקצב.

### Handlers — שימוש ב-helpers קיימים
- לחילוץ פרטי משתמש — `_get_user_info(update)`. לא לשכפל את הלוגיקה ידנית.

### צ'ק ליסט הקלטת לקוח — לעדכן בכל שינוי רלוונטי
- המסמך `docs/client_checklist.md` מתאר את תהליך ההקלטה ללקוח חדש.
- בכל שינוי ב-`seed_data.py` (קטגוריות, שדות, מבנה), `config.py` (משתני סביבה, system prompt), `.env.example`, או פיצ'רים בבוט/אדמין — **יש לעדכן גם את הצ'ק ליסט** כדי שישקף את המצב הנוכחי של הקוד.

### טסטים — כיסוי ותחזוקה
- **הרצה:** `python -m pytest tests/ -v`
- **מבנה:** קובץ טסט לכל מודול — `tests/test_<module>.py`. fixtures משותפים ב-`tests/conftest.py`.
- **DB בטסטים:** כל טסט מקבל DB זמני נפרד (tmp_path). לעולם לא לגעת ב-DB אמיתי.
- **תלויות חיצוניות:** מודולים שתלויים ב-telegram / OpenAI — mock לפני ייבוא. לא לקרוא ל-API בטסטים.
- **כשמוסיפים לוגיקה חדשה:** להוסיף טסט באותו commit. עדיפות למודולים עם לוגיקה טהורה (intent, chunker, rate_limiter, business_hours).

## פקודות

```bash
# הרצת הפרויקט (בוט + אדמין)
python main.py

# בוט בלבד
python main.py --bot

# אדמין בלבד
python main.py --admin

# Seed data
python main.py --seed

# טסטים
python -m pytest tests/ -v
```
