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

### Handlers — צינור RAG אחד בלבד
- כל נתיב שמפעיל את צינור ה-RAG (כולל callback queries) חייב לעבור דרך `_handle_rag_query` ולא לשכפל את הלוגיקה. לצורך callbacks בלי `update.message` — להעביר `chat_id`.

### Handlers — rate limit על כל קריאת LLM
- כל נתיב שמגיע ל-LLM (הודעות, callbacks, שאלות המשך) חייב לעבור בדיקת `check_rate_limit` + `record_message`. ללא זה משתמש יכול לעקוף את מגבלות הקצב.

### Handlers — שימוש ב-helpers קיימים
- לחילוץ פרטי משתמש — `_get_user_info(update)`. לא לשכפל את הלוגיקה ידנית.

### צ'ק ליסט הקלטת לקוח — לעדכן בכל שינוי רלוונטי
- המסמך `docs/client_checklist.md` מתאר את תהליך ההקלטה ללקוח חדש.
- בכל שינוי ב-`seed_data.py` (קטגוריות, שדות, מבנה), `config.py` (משתני סביבה, system prompt), `.env.example`, או פיצ'רים בבוט/אדמין — **יש לעדכן גם את הצ'ק ליסט** כדי שישקף את המצב הנוכחי של הקוד.

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
```
