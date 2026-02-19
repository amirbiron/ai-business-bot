# סקירת קוד — AI Business Bot

**תאריך:** 2026-02-19
**סוקר:** Claude (סקירת קוד אוטומטית)
**פרויקט:** צ'אטבוט עסקי מבוסס AI (בוט טלגרם + פאנל ניהול + מנוע RAG)

---

## תקציר

הפרויקט הוא צ'אטבוט שירות לקוחות מבוסס AI לעסקים קטנים, הכולל ממשק בוט טלגרם, פאנל ניהול Flask, ומנוע RAG (Retrieval-Augmented Generation) מבוסס FAISS. הקוד נקי, מודולרי, ומדגים פרקטיקות הנדסת תוכנה טובות. להלן סקירה מפורטת לפי רמת חומרה.

---

## ממצאים קריטיים

### 1. אבטחה: השוואת סיסמאות בטקסט גלוי (admin/app.py:66)

```python
if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
```

**בעיה:** סיסמאות מושוות באמצעות השוואת מחרוזות ישירה (`==`), מה שחושף את המערכת למתקפות תזמון (timing attacks). בנוסף, הסיסמה נשמרת כטקסט גלוי במשתני סביבה.

**המלצה:**
- להשתמש ב-`hmac.compare_digest()` להשוואה בזמן קבוע.
- לאחסן סיסמאות עם hash באמצעות `bcrypt` או `werkzeug.security.check_password_hash()`.

```python
from werkzeug.security import check_password_hash
if username == ADMIN_USERNAME and check_password_hash(stored_hash, password):
```

### 2. אבטחה: סודות ברירת מחדל מוטמעים בקוד (config.py:42-43)

```python
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme123")
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "super-secret-key-change-me")
```

**בעיה:** סיסמאות ומפתחות סודיים מוטמעים בקוד כברירת מחדל. אם קובץ `.env` חסר או חלקי, האפליקציה תרוץ עם פרטי גישה ידועים לכל מי שקורא את קוד המקור.

**המלצה:**
- להסיר ערכי ברירת מחדל לשדות רגישים או לזרוק שגיאה אם לא הוגדרו.
- להוסיף בדיקה בעלייה שמסרבת לרוץ עם פרטי גישה ברירת מחדל בסביבת ייצור.

```python
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    raise ValueError("ADMIN_PASSWORD must be set in environment variables")
```

### 3. אבטחה: Deserialization לא בטוח עם Pickle (vector_store.py:148-149)

```python
with open(metadata_file, "rb") as f:
    self.metadata = pickle.load(f)
```

**בעיה:** `pickle.load()` יכול להריץ קוד שרירותי. אם קובץ ה-metadata שונה בזדון (למשל על ידי תוקף עם הרשאות כתיבה לתיקיית הנתונים), זהו וקטור להרצת קוד מרחוק (RCE).

**המלצה:** להחליף pickle בפורמט סריאליזציה בטוח יותר כמו JSON.

```python
with open(metadata_file, "r") as f:
    self.metadata = json.load(f)
```

### 4. אבטחה: אין הגנת CSRF (admin/app.py)

**בעיה:** פאנל הניהול של Flask משתמש בטפסי POST לשינוי נתונים (CRUD של בסיס ידע, עדכוני סטטוס, בניית אינדקס מחדש) אך ללא הגנת CSRF כלל.

**המלצה:** לשלב `Flask-WTF` ליצירה ואימות של CSRF tokens.

```python
from flask_wtf.csrf import CSRFProtect
csrf = CSRFProtect(app)
```

### 5. אבטחה: אין ולידציה על קלט בעדכוני סטטוס (admin/app.py:229, 249)

```python
status = request.form.get("status", "handled")
db.update_agent_request_status(request_id, status)
```

**בעיה:** ערך ה-`status` מגיע ישירות מקלט המשתמש ללא ולידציה. למרות ש-CHECK constraints ב-SQLite מספקים הגנה מסוימת, האפליקציה צריכה לוודא ערכי סטטוס לפני העברתם לבסיס הנתונים.

**המלצה:**
```python
VALID_REQUEST_STATUSES = {"pending", "handled", "dismissed"}
status = request.form.get("status", "handled")
if status not in VALID_REQUEST_STATUSES:
    flash("סטטוס לא חוקי.", "danger")
    return redirect(url_for("agent_requests"))
```

---

## ממצאים בעדיפות גבוהה

### 6. אמינות: אין Connection Pooling / Context Manager לבסיס הנתונים (database.py)

**בעיה:** כל פונקציית בסיס נתונים פותחת חיבור חדש, מבצעת פעולה, וסוגרת אותו. אין connection pooling ואין שימוש ב-context managers (פקודות `with`). אם חריגה מתרחשת בין `get_connection()` ל-`conn.close()`, החיבור "דולף".

**המלצה:** להשתמש בתבנית context manager:

```python
from contextlib import contextmanager

@contextmanager
def get_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

### 7. אמינות: אתחול גלובלי של OpenAI Client בזמן Import (llm.py:24, embeddings.py:22)

```python
client = OpenAI()
```

**בעיה:** לקוח OpenAI מאותחל בזמן ייבוא המודול. אם `OPENAI_API_KEY` לא מוגדר, הייבוא עצמו ייכשל (או שהלקוח ייווצר במצב לא תקין), מה שחוסם את כל האפליקציה — גם כשצריך רק את פאנל הניהול.

**המלצה:** להשתמש באתחול עצלן (lazy initialization):

```python
_client = None

def get_client():
    global _client
    if _client is None:
        _client = OpenAI()
    return _client
```

### 8. אמינות: קריאות LLM סינכרוניות בתוך Handlers אסינכרוניים (bot/handlers.py:357)

```python
result = generate_answer(
    user_query=user_message,
    conversation_history=history,
)
```

**בעיה:** `generate_answer()` מבצע קריאות HTTP סינכרוניות ל-API של OpenAI בתוך handler אסינכרוני (`async`). זה חוסם את כל לולאת האירועים ומונע מהבוט לעבד הודעות של משתמשים אחרים במהלך קריאת ה-API (שיכולה לקחת שניות).

**המלצה:** להריץ את הקריאה החוסמת ב-thread executor:

```python
import asyncio

loop = asyncio.get_event_loop()
result = await loop.run_in_executor(None, lambda: generate_answer(
    user_query=user_message,
    conversation_history=history,
))
```

### 9. שלמות נתונים: אינדקס RAG לא נבנה מחדש אחרי שינויים ב-KB (admin/app.py:133, 162)

**בעיה:** כאשר רשומת KB נוספת, נערכת או נמחקת דרך פאנל הניהול, אינדקס FAISS לא נבנה מחדש אוטומטית. הבוט ימשיך להשתמש בנתונים ישנים עד שמישהו ילחץ ידנית על "בנה אינדקס מחדש".

**המלצה:** אחת מהאפשרויות הבאות:
- בנייה מחדש אוטומטית אחרי שינויים ב-KB (עלול להיות איטי).
- הצגת באנר אזהרה ברור כשהאינדקס לא מעודכן.
- תזמון משימת בנייה מחדש ברקע.

### 10. אמינות: ערך ההחזרה של `booking_start` לא מטופל כראוי (bot/handlers.py:339-340)

```python
elif user_message == "📅 קביעת תור":
    return await booking_start(update, context)
```

**בעיה:** כאשר טקסט כפתור ההזמנה נתפס על ידי `message_handler`, הקריאה ל-`booking_start` מחזירה את מצב `BOOKING_SERVICE`, אבל ה-`ConversationHandler` לא מודע לכך מכיוון שההודעה הונתבה דרך ה-handler הכללי. זה אומר שתהליך ההזמנה עלול לא לעבוד נכון במקרה קצה זה.

**המלצה:** להסיר את הניתוב החלופי הזה מ-`message_handler` — ה-`ConversationHandler` צריך לטפל בו באופן בלעדי.

---

## ממצאים בעדיפות בינונית

### 11. ביצועים: הדשבורד טוען את כל הנתונים רק כדי לספור (admin/app.py:84-88)

```python
kb_entries = db.get_all_kb_entries()
users = db.get_unique_users()
pending_requests = db.get_agent_requests(status="pending")
pending_appointments = db.get_appointments(status="pending")
stats = {
    "kb_entries": len(kb_entries),
    ...
}
```

**בעיה:** הדשבורד מביא את כל הרשומות רק כדי לספור אותן. ככל שהנתונים גדלים, זה לא יעיל.

**המלצה:** להוסיף פונקציות ספירה ייעודיות ב-`database.py`:

```python
def count_kb_entries() -> int:
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM kb_entries WHERE is_active=1").fetchone()[0]
    conn.close()
    return count
```

### 12. עמידות: שגיאות Markdown לא מטופלות (bot/handlers.py:366)

```python
await update.message.reply_text(
    result["answer"],
    parse_mode="Markdown",
    ...
)
```

**בעיה:** אם תשובת ה-LLM מכילה תווי Markdown לא תקינים (למשל `*`, `_`, `[` ללא סגירה), טלגרם ידחה את ההודעה עם שגיאת `BadRequest`.

**המלצה:** לעטוף ב-try/except ולחזור לטקסט רגיל:

```python
try:
    await update.message.reply_text(result["answer"], parse_mode="Markdown", ...)
except telegram.error.BadRequest:
    await update.message.reply_text(result["answer"], reply_markup=_get_main_keyboard())
```

### 13. ארכיטקטורה: הערכת טוקנים לא מדויקת לעברית (rag/chunker.py:11)

```python
def estimate_tokens(text: str) -> int:
    return len(text) // 4
```

**בעיה:** ההיוריסטיקה "4 תווים לכל טוקן" מתאימה לאנגלית. טקסט בעברית מייצר בדרך כלל 2-3 תווים לכל טוקן ברוב ה-tokenizers, מה שאומר שהצ'אנקים עלולים להיות גדולים משמעותית מהמתוכנן.

**המלצה:** להשתמש ב-`tiktoken` לספירת טוקנים מדויקת:

```python
import tiktoken
enc = tiktoken.encoding_for_model("gpt-4")
def estimate_tokens(text: str) -> int:
    return len(enc.encode(text))
```

### 14. ניטור: שימוש ב-f-string בקריאות Logger (קבצים מרובים)

```python
logger.warning(f"Quality check failed — ...")
logger.error(f"LLM API error: {e}")
```

**בעיה:** שימוש ב-f-strings בקריאות logging גורם לעיצוב המחרוזת תמיד, גם אם רמת הלוג מבוטלת. זו בעיית ביצועים קלה ומאבדת יכולות structured logging.

**המלצה:** להשתמש בעיצוב עצלן (lazy formatting):

```python
logger.warning("Quality check failed — no source citation. Response: '%s...'", response_text[:100])
```

### 15. ארכיטקטורה: בטיחות תהליכונים של Flask Admin + Telegram Bot (main.py:85)

```python
admin_thread = threading.Thread(target=run_admin_panel, daemon=True)
admin_thread.start()
```

**בעיה:** שרת הפיתוח של Flask ובוט הטלגרם חולקים את אותו תהליך. SQLite, למרות תמיכה במצב WAL, עלול להיתקל בבעיות עם כתיבות מקביליות ממספר תהליכונים. בנוסף, שרת הפיתוח של Flask אינו thread-safe כברירת מחדל.

**המלצה:**
- להשתמש ב-Gunicorn עבור פאנל הניהול (כפי שמוגדר ב-`render.yaml` לסביבת ייצור).
- לשקול שימוש מפורש ב-`check_same_thread=False` בחיבור SQLite בהרצה מרובת תהליכונים.

### 16. נתונים: באג בסדר היסטוריית שיחות (database.py:215-222)

```python
rows = conn.execute(
    """SELECT ... ORDER BY created_at DESC LIMIT ?""",
    (user_id, limit)
).fetchall()
return [dict(r) for r in reversed(rows)]
```

**בעיה:** השאילתה ממיינת לפי `created_at DESC` ואז הופכת ב-Python לסדר כרונולוגי. אולם, `created_at` משתמש ב-`datetime('now')` שיש לו דיוק ברמת שנייה. אם שתי הודעות נשמרות באותה שנייה (למשל הודעת משתמש ותשובת בוט), הסדר ביניהן אינו מוגדר.

**המלצה:** למיין לפי `id` במקום `created_at`, מכיוון ש-autoincrement מבטיח סדר הכנסה:

```python
ORDER BY id DESC LIMIT ?
```

---

## ממצאים בעדיפות נמוכה / איכות קוד

### 17. קונפיגורציה: `ADMIN_HOST = "0.0.0.0"` (config.py:44)

**בעיה:** הקשרה (binding) ל-`0.0.0.0` הופכת את פאנל הניהול לנגיש מכל ממשק רשת, מה שמתאים לייצור מאחורי reverse proxy אבל מסוכן בפיתוח.

**המלצה:** ברירת מחדל `127.0.0.1` לפיתוח ודריסה בייצור.

### 18. איכות קוד: ייבואים שאינם בשימוש (bot/handlers.py:19-20)

```python
from telegram import (
    ...
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
```

**בעיה:** `InlineKeyboardButton` ו-`InlineKeyboardMarkup` מיובאים אך לא נמצאים בשימוש ב-handlers.

**המלצה:** להסיר ייבואים שאינם בשימוש.

### 19. איכות קוד: מספרי קסם (llm.py:62, 139-140)

```python
for msg in conversation_history[-10:]:  # Keep last 10 messages
...
temperature=0.3,
max_tokens=500,
```

**בעיה:** מספרי קסם עבור מגבלת היסטוריה, טמפרטורה ומקסימום טוקנים מוטמעים בקוד.

**המלצה:** להעביר ל-`config.py` כקבועים בעלי שם:

```python
CONVERSATION_HISTORY_LIMIT = 10
LLM_TEMPERATURE = 0.3
LLM_MAX_TOKENS = 500
```

### 20. איכות קוד: תיקיית static לא קיימת (admin/app.py:45)

```python
app = Flask(__name__, template_folder="templates", static_folder="static")
```

**בעיה:** תיקיית `static` לא קיימת בתיקיית `admin/`. Flask יטפל בכך בשקט אך ירשום שגיאות 404 אם תבנית כלשהי מפנה לקבצים סטטיים.

**המלצה:** ליצור את תיקיית `static/` או להסיר את הפרמטר `static_folder`.

### 21. ארכיטקטורה: מבנה חבילה כפול

**בעיה:** ב-repository יש גם תיקיות ברמת השורש (`admin/`, `bot/`, `rag/`, `utils/`) וגם חבילת `ai_chatbot/` עם אותו מבנה. הייבואים משתמשים ב-`ai_chatbot.*`, מה שמרמז שתיקיות רמת השורש הן כנראה שרידים ישנים מ-refactor.

**המלצה:** להסיר את תיקיות רמת השורש הכפולות אם אינן הקוד הראשי, או לאחד למבנה אחד.

### 22. בדיקות: אין סט בדיקות

**בעיה:** אין בדיקות בפרויקט. מנוע ה-RAG, אינטגרציית ה-LLM, פעולות בסיס הנתונים ו-handlers של הבוט — כולם חסרים בדיקות יחידה או אינטגרציה.

**המלצה:** להוסיף לפחות:
- בדיקות יחידה ל-`chunker.py` (לוגיקה טהורה, קל לבדוק).
- בדיקות יחידה ל-`database.py` עם SQLite בזיכרון.
- בדיקות אינטגרציה למנוע ה-RAG עם embeddings מדומים.
- בדיקות handlers באמצעות כלי הבדיקה של `python-telegram-bot`.

### 23. טיפול בשגיאות: `error_handler` מתעד אך לא מבדיל (bot/handlers.py:375-384)

**בעיה:** כל השגיאות מתועדות באותה רמה עם אותה הודעה למשתמש. שגיאות רשת, שגיאות API ובאגים בתכנות — כולם נראים אותו דבר.

**המלצה:** להוסיף הבחנה לפי סוג שגיאה:

```python
async def error_handler(update, context):
    if isinstance(context.error, telegram.error.NetworkError):
        logger.warning("Network error: %s", context.error)
    elif isinstance(context.error, telegram.error.TimedOut):
        logger.warning("Request timed out")
    else:
        logger.error("Unexpected error: %s", context.error, exc_info=True)
```

---

## חוזקות

לפרויקט מספר חוזקות בולטות שראוי לציין:

1. **ארכיטקטורת שלוש שכבות** — ההפרדה בין System Prompt (שכבה A), הקשר RAG (שכבה B), ובדיקת איכות (שכבה C) היא תבנית מוצקה למניעת הזיות ולהבטחת איכות תשובות.

2. **הפרדת מודולים נקייה** — כל רכיב (bot, admin, rag, database) בעל אחריות ברורה וממשקים מוגדרים היטב.

3. **חזרה חיננית (Graceful Fallbacks)** — המעבר של מודול ה-embeddings ל-embeddings מקומיים מבוססי hash מאפשר פיתוח אופליין. מודול ה-LLM מחזיר תשובת ברירת מחדל בטוחה בשגיאות.

4. **עיצוב בסיס נתונים טוב** — מצב WAL, מפתחות זרים, CHECK constraints ואינדוקס נכון מדגימים שימוש מוצק ב-SQLite.

5. **חוויית משתמש בעברית** — ה-System Prompt, טקסט ה-UI ואינטראקציות המשתמש כולם בעברית, מה שמראה תשומת לב לקהל היעד.

6. **מוכן לפריסה** — קונפיגורציית `render.yaml`, תמיכה ב-Gunicorn, וקונפיגורציה מבוססת משתני סביבה מראים מוכנות לייצור.

7. **רצף שיחה** — שמירה ואחזור של היסטוריית שיחות לתשובות מודעות הקשר היא פרקטיקה טובה לחוויית משתמש.

---

## טבלת סיכום

| # | חומרה | קובץ | ממצא |
|---|--------|------|------|
| 1 | קריטי | admin/app.py | השוואת סיסמאות בטקסט גלוי |
| 2 | קריטי | config.py | סודות ברירת מחדל מוטמעים בקוד |
| 3 | קריטי | vector_store.py | deserialization לא בטוח עם pickle |
| 4 | קריטי | admin/app.py | אין הגנת CSRF |
| 5 | קריטי | admin/app.py | אין ולידציה על קלט בעדכוני סטטוס |
| 6 | גבוה | database.py | אין connection pooling / context manager |
| 7 | גבוה | llm.py, embeddings.py | אתחול גלובלי של client בזמן import |
| 8 | גבוה | bot/handlers.py | קריאות LLM סינכרוניות חוסמות event loop אסינכרוני |
| 9 | גבוה | admin/app.py | אינדקס RAG מיושן אחרי שינויים ב-KB |
| 10 | גבוה | bot/handlers.py | מקרה קצה בניתוב תהליך הזמנה |
| 11 | בינוני | admin/app.py | הדשבורד טוען את כל הנתונים רק כדי לספור |
| 12 | בינוני | bot/handlers.py | שגיאות Markdown לא מטופלות |
| 13 | בינוני | rag/chunker.py | הערכת טוקנים לא מדויקת לעברית |
| 14 | בינוני | מרובים | שימוש ב-f-string בקריאות logger |
| 15 | בינוני | main.py | חששות בטיחות תהליכונים |
| 16 | בינוני | database.py | מיון היסטוריית שיחות לפי timestamp |
| 17 | נמוך | config.py | 0.0.0.0 כ-host ברירת מחדל |
| 18 | נמוך | bot/handlers.py | ייבואים שאינם בשימוש |
| 19 | נמוך | llm.py | מספרי קסם |
| 20 | נמוך | admin/app.py | תיקיית static לא קיימת |
| 21 | נמוך | שורש | מבנה חבילה כפול |
| 22 | נמוך | פרויקט | אין סט בדיקות |
| 23 | נמוך | bot/handlers.py | טיפול גנרי בשגיאות |

---

**הערכה כוללת:** הקוד מאורגן היטב ומדגים החלטות ארכיטקטוניות מוצקות (מנוע RAG, LLM תלת-שכבתי, עיצוב מודולרי). תחומי השיפור העיקריים הם חיזוק אבטחה (אימות, CSRF, pickle), אמינות תחת מקביליות (async/await, חיבורי DB), והוספת סט בדיקות. עם טיפול בשיפורים אלו, זו תהיה מערכת ייצור חזקה ואמינה.
