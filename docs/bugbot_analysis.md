# אנליזת באגים שנתפסו בסקירות קוד — ai-business-bot

> נוצר אוטומטית מניתוח היסטוריית git. 45 באגים מתועדים.

---

## סיכום: 10 דפוסי באגים שה-bugbot תופס

### 1. ולידציית קלט (8 באגים)
- הסרת ולידציית דפדפן בלי תחליף בשרת (25:99 → קריסה)
- אורך הודעת broadcast לא נבדק → כשל שקט ל-1000 נמענים
- ממד embedding לא נבדק → קריסה קריפטית ב-FAISS
- חוסר UNIQUE על appointments ו-special_days

### 2. לוגיקה עסקית / מקרי קצה (9 באגים)
- משמרת לילה של אתמול לא נבדקת (22:00–02:00)
- השוואת שעות עם wraparound שבורה
- ערב חג דורס יום סגור רגיל
- גבול שנה חסר בבדיקת חגים (31/12 → 1/1)

### 3. עקביות DB (7 באגים)
- ולידציה באמצע לולאה → עדכון חלקי
- שמירת הודעה ל-DB לפני אישור שליחה ב-Telegram
- שימוש חוזר ב-embedding לפי אינדקס בלי להשוות טקסט

### 4. ניהול מצב / concurrency (7 באגים)
- sessions של צ'אט חי שורדים restart → בוט שותק לצמיתות
- ספירת rate limit כפולה בקריאות פנימיות
- סדר decorators שגוי (rate limit לפני live chat guard)
- תגובת poll דורסת הודעה שנשלחה

### 5. async / control flow (5 באגים)
- קריאת DB סינכרונית חוסמת event loop
- spinner של callback query לא נסגר
- polling ללא session פעיל (כל 3 שניות לחינם)

### 6. בטיחות נתונים (6 באגים)
- quality check מקבל מקורות מזויפים מה-LLM
- API key עלול להופיע בלוגים
- fallback embeddings מחזיר תוצאות חסרות משמעות בשקט

---

## רשימה מלאה — באגים לפי חומרה

### באגים קריטיים (MAJOR — 10)

#### 1. ולידציית שעות הוסרה → ערכים לא חוקיים גורמים לקריסה
- **Commit:** `4590e5e0bcb1b21`
- **בעיה:** שינוי input מ-`type="time"` ל-`type="text"` הסיר ולידציית דפדפן. ערכים כמו "25:99" נשמרים ל-DB.
- **קריסה:** `time.fromisoformat()` מתרסק בקריאה חוזרת.
- **תיקון:** ולידציה בצד שרת עם regex (HH:MM, 00:00–23:59) + try/except fallback.

#### 2. ולידציה באמצע לולאה → עדכון חלקי ב-DB
- **Commit:** `48a750dea96abe39`
- **בעיה:** לולאת `business_hours_update` קוראת וכותבת ל-DB בכל איטרציה.
- **תוצאה:** כשל ביום 5/7 — ימים 1-4 כבר נכתבו. DB לא עקבי.
- **תיקון:** כל הולידציה לפני כתיבה ל-DB (transaction-like pattern).

#### 3. ערב חג דורס בדיקת יום סגור
- **Commit:** `e3711c45de8ebb9f`
- **בעיה:** לוגיקת חגים בדקה ערב חג לפני לוח שבועי רגיל.
- **תוצאה:** שבת + ערב חג → מדווח "פתוח" במקום "סגור".
- **תיקון:** לבדוק לוח שבועי קודם.

#### 4. חיפוש פתיחה הבא מדלג על ימי ערב חג
- **Commit:** `e3711c45de8ebb9f`
- **בעיה:** `_find_next_opening` לא מעביר שעות פתיחה/סגירה לסטטוס ערב חג.
- **תיקון:** סטטוס ערב חג כולל עכשיו נתוני שעות.

#### 5. גבול שנה חסר בזיהוי חגים
- **Commit:** `e3711c45de8ebb9f`
- **בעיה:** `_get_israeli_holidays` בודק רק שנה נוכחית.
- **תוצאה:** ערב חג 31/12 → 1/1 לא מזוהה.
- **תיקון:** פונקציה מקבלת מספר שנים.

#### 6. שימוש חוזר ב-embedding בלי השוואת טקסט
- **מקור:** Code Review R4
- **בעיה:** RAG engine משתמש ב-embedding קיים לפי `chunk_index` בלבד.
- **תוצאה:** אם תוכן ה-chunk השתנה אבל האינדקס נשאר — embedding ישן בשימוש.
- **תיקון:** להשוות `chunk["text"] == old_chunk["text"]` לפני שימוש חוזר.

#### 7. שעות פעילות לא ממוזגות לקונטקסט RAG
- **Commit:** `41134ec2bf61f5ea`
- **בעיה:** הודעת RAG אומרת "השתמש **רק** במידע שלמעלה" ואז מוסיפה שעות בהודעה נפרדת.
- **תוצאה:** LLM מתעלם משעות בגלל ההנחיה הבלעדית.
- **תיקון:** מיזוג chunks + שעות להודעת מערכת אחת.

#### 8. sessions צ'אט חי שורדים restart → בוט שותק
- **Commit:** `4d3016abe071d1cf`
- **בעיה:** sessions עם `is_active=1` שורדים restart.
- **תוצאה:** כל handler בודק `is_live_chat_active()` → בוט שותק לצמיתות למשתמשים אלו.
- **תיקון:** ניקוי sessions ב-`init_db()` בעליית המערכת.

#### 9. ספירת rate limit כפולה בקריאות פנימיות
- **Commit:** `800069acc4ece893`
- **בעיה:** `message_handler` ו-`booking_button_interrupt` קוראים ל-handlers מעוטרים פנימית.
- **תוצאה:** `record_message` יורה פעמיים → rate limit שבור.
- **תיקון:** שימוש ב-`__wrapped__` לעקוף `rate_limit_guard` בקריאות פנימיות.

#### 10. סדר guards שגוי — rate limit לפני live chat
- **Commit:** `800069acc4ece893`
- **בעיה:** `rate_limit_guard` רץ לפני `live_chat_guard`.
- **תוצאה:** משתמשים בצ'אט חי נחסמים ב-rate limit במקום שהודעותיהם יישמרו לנציג.
- **תיקון:** שינוי סדר דקורטורים — `live_chat_guard` ראשון.

---

### באגים בינוניים (MEDIUM — 15)

#### 11. משמרת לילה אתמול לא נבדקת
- **Commit:** `f149cbe736d67753`
- **בעיה:** `is_currently_open()` בודק רק היום.
- **תוצאה:** שני 22:00–02:00 → בשלישי 01:00 מדווח "סגור".
- **תיקון:** בדיקת לוח אתמול לפני היום.

#### 12. השוואת שעות wraparound שבורה
- **Commit:** `6273a65fdc80aa1c`
- **בעיה:** `23:00 >= 22:00 AND 23:00 < 02:00` — תנאי שני נכשל.
- **תיקון:** זיהוי `close_time < open_time` וטיפול ב-wraparound.

#### 13. מצב follow-up לא מנוקה → דליפת זיכרון
- **מקור:** Code Review H6
- **בעיה:** dictionary של follow-up states גדל ללא גבול.
- **תיקון:** ניקוי ערכים ישנים.

#### 14. Intent עוקף state machine של ConversationHandler
- **Commit:** `514ff43f01a2721`
- **בעיה:** `APPOINTMENT_BOOKING` intent קורא ל-`booking_start()` ישירות.
- **תוצאה:** עוקף `ConversationHandler` → שובר booking מרובה-שלבים.
- **תיקון:** Intent מכוון משתמש ללחוץ כפתור במקום לקרוא ישירות.

#### 15. התאמות intent שגויות — "schedule" ו-"cancellation"
- **Commit:** `514ff43f01a2721`
- **בעיה:** מילות מפתח ללא word boundaries ב-regex.
- **תוצאה:** "מה ה-schedule שלכם?" מפעיל booking; "מדיניות cancellation?" מפעיל ביטול.
- **תיקון:** word boundaries או patterns ספציפיים יותר.

#### 16. נתיב pricing משכפל שאילתת משתמש בקונטקסט LLM
- **Commit:** `514ff43f01a2721`
- **בעיה:** שומר הודעה נוכחית לפני שליפת היסטוריה.
- **תוצאה:** שאילתה מופיעה פעמיים → מבלבל LLM.
- **תיקון:** שליפת היסטוריה לפני שמירה.

#### 17. מחיקת HTMX משאירה שורת עריכה יתומה
- **Commit:** `3eabcdc73e24c49c`
- **בעיה:** מחיקה מכוונת רק לשורת תצוגה, לא לשורת עריכה צמודה.
- **תוצאה:** טופס עריכה יתום שולח POST למשאב שנמחק.
- **תיקון:** עטיפת שורות ב-`<tbody>` משותף כ-target.

#### 18. Spinner של callback query לא נסגר
- **Commit:** `6b05e25b07321936`
- **בעיה:** `cancel_appointment_callback` חוזר לפני `answer_callback_query()`.
- **תוצאה:** spinner מסתובב ~30 שניות עד timeout.
- **תיקון:** מענה ל-callback לפני guard decorators.

#### 19. חוסר UNIQUE constraint על appointments
- **מקור:** Code Review D6, Commit: `0a29eebddfc7bd7b`
- **בעיה:** `appointments(user_id, preferred_date, preferred_time)` ללא unique.
- **תוצאה:** משתמש יכול להזמין אותו slot פעמיים.
- **תיקון:** UNIQUE partial index + מיגרציה לדדופ.

#### 20. חוסר UNIQUE constraint על special_days
- **Commit:** `6273a65fdc80aa1c`
- **בעיה:** `special_days.date` מאפשר כפילויות.
- **תוצאה:** אדמין מוסיף יום, מוסיף שוב → כפילות שקטה.
- **תיקון:** UNIQUE constraint + INSERT OR REPLACE.

#### 21. תגובת poll דורסת הודעה שנשלחה
- **Commit:** `05ce785b8268d83d`
- **בעיה:** polling ושליחה ללא `hx-sync`.
- **תוצאה:** תגובת poll ישנה מגיעה אחרי שליחה → הודעה נעלמת ל-3 שניות.
- **תיקון:** `hx-sync="#live-chat-messages:replace"` על טופס שליחה.

#### 22. הודעות נשמרות ללא אישור שליחה ב-Telegram
- **Commit:** `2ad4c227e1729a46`
- **בעיה:** `_do_start_live_chat` ו-`live_chat_end` שומרים התראות ללא תנאי.
- **תוצאה:** היסטוריה מציגה הודעות שהלקוח לא קיבל.
- **תיקון:** שמירה רק כשהשליחה ב-Telegram מצליחה.

#### 23. חוסר עקביות ב-HTML escaping
- **מקור:** Code Review H8
- **בעיה:** שימוש מעורב ב-`html.escape()` ו-`sanitize_telegram_html()`.
- **תיקון:** פונקציית escaping אחידה.

#### 24. שגיאת syntax במיגרציית SQLite
- **Commit:** `e8779fedae15b1e6`
- **בעיה:** `ALTER TABLE ADD COLUMN DEFAULT (datetime('now'))`.
- **תוצאה:** SQLite לא תומך ב-DEFAULT לא-קבוע.
- **תיקון:** DEFAULT קבוע + back-fill מעמודה אחרת.

#### 25. Quality check מקבל מקורות מזויפים
- **מקור:** Code Review L5
- **בעיה:** pattern `([Ss]ource|מקור):\s*.+` מקבל כל טקסט.
- **תוצאה:** LLM כותב "מקור: לפי הידע שלי" ועובר בדיקה.
- **תיקון:** ולידציה מול מקורות אמיתיים.

---

### באגים קלים (LOW — 20)

#### 26. קריאת DB סינכרונית חוסמת event loop
- **Commit:** `03506ebafadb9a30`
- **תיקון:** `asyncio.to_thread(db.update...)`.

#### 27. הודעת broadcast לא מאומתת לאורך/פורמט
- **Commit:** `03506ebafadb9a30`
- **תוצאה:** הודעה > 4096 תווים נכשלת שקט ל-1000 נמענים.
- **תיקון:** ולידציית אורך לפני שליחה.

#### 28. דדופליקציה שקטה במיגרציות
- **מקור:** Code Review D8
- **תוצאה:** נתונים נמחקים ללא לוג.
- **תיקון:** הוספת warning log.

#### 29. O(n²) בהתאמת chunks ב-RAG
- **מקור:** Code Review R5
- **תיקון:** שימוש ב-dict lookup.

#### 30. חוסר אינדקסים על conversations
- **מקור:** Code Review D4
- **תיקון:** אינדקס מורכב על `(user_id, created_at)`.

#### 31. ממד embedding לא נבדק לפני חיפוש FAISS
- **מקור:** Code Review E8
- **תיקון:** ולידציית dimension מול אינדקס.

#### 32. חוסר ולידציית אורך metadata
- **מקור:** Code Review E7
- **תיקון:** בדיקה ב-`build_index`.

#### 33. API key עלול להופיע בהודעות exception
- **מקור:** Code Review E9
- **תיקון:** ניקוי לפני logging.

#### 34. Race condition באתחול OpenAI client
- **מקור:** Code Review E2
- **תיקון:** lock סביב אתחול.

#### 35. Fallback embeddings לא סמנטיים
- **מקור:** Code Review E1
- **בעיה:** fallback מבוסס hash כש-API לא זמין → תוצאות חסרות משמעות.
- **תיקון:** הודעת שגיאה במקום degradation שקט.

#### 36. שם משתנה מסתיר built-in
- **מקור:** Code Review H3
- **בעיה:** `time = ...` מסתיר מודול `time`.
- **תיקון:** שינוי ל-`preferred_time`.

#### 37. שאילתות SQL ב-handlers
- **מקור:** Code Review H4
- **תיקון:** העברה ל-`database.py`.

#### 38. Fallback embeddings — mutation לא צפוי
- **מקור:** Code Review E3
- **בעיה:** `faiss.normalize_L2` משנה array במקום.
- **תיקון:** clone לפני נורמליזציה.

#### 39. User ID לא מאומת ב-live chat routes
- **מקור:** Code Review A6
- **תיקון:** regex check לפורמט Telegram ID.

#### 40. Session timeout ארוך מדי (30 יום)
- **מקור:** Code Review A1
- **תיקון:** הקטנה ל-7 ימים.

#### 41. Polling ללא session פעיל
- **Commit:** `ff00265898fe6d5e`
- **בעיה:** HTMX polling כל 3 שניות גם בלי session.
- **תיקון:** התניית polling על `live_session`.

#### 42. מיקום scroll נאבד ב-polling צ'אט חי
- **Commit:** `8895dcc65a94e4ae`
- **בעיה:** DOM replacement גולל למטה בכל poll.
- **תיקון:** auto-scroll רק אם כבר קרוב לתחתית (80px threshold).

#### 43. Username ריק לא מאומת
- **Commit:** `f521c946a459b55f`
- **בעיה:** בודק קיום אבל לא truthiness.
- **תיקון:** `username.strip()`.

#### 44. גישה שברירית ל-`__wrapped__`
- **מקור:** Code Review H1
- **בעיה:** שינוי סדר דקורטורים שובר כל נתיבי `__wrapped__`.
- **תיקון:** פונקציות `_inner()` מפורשות.

#### 45. שכפול לוגיקת live chat start
- **מקור:** Code Review H2
- **בעיה:** `message_handler` ו-`booking_button_interrupt` שניהם מנתבים דרך `__wrapped__`.
- **תיקון:** איחוד לוגיקת ניתוב.

---

## קטגוריזציה לפי דפוס

| דפוס | כמות | דוגמאות מרכזיות |
|---|---|---|
| ולידציית קלט | 8 | שעות, אורך broadcast, ממד embedding, UNIQUE |
| לוגיקה עסקית / מקרי קצה | 9 | overnight, ערב חג, גבול שנה, intent |
| עקביות DB | 7 | עדכון חלקי, embedding ישן, הודעות בלי אישור |
| ניהול מצב / concurrency | 7 | sessions, rate limit כפול, poll דורס |
| async / control flow | 5 | DB סינכרוני, spinner, polling מיותר |
| בטיחות נתונים | 6 | מקורות מזויפים, API key, escaping |
| סכימת DB | 2 | syntax error, DEFAULT לא חוקי |
| DOM/HTMX | 3 | שורה יתומה, מיזוג context, scroll |

---

## השוואה: Bugbot מול Claude Code

| Bugbot חזק ב- | Claude Code חזק ב- |
|---|---|
| מקרי קצה בזמן ריצה | ארכיטקטורה ומבנה |
| אילוצי DB חסרים | כיסוי טסטים |
| concurrency ו-race conditions | אבטחה כללית |
| data flow מקצה לקצה | ביצועים |
| atomicity ועקביות מצב | type safety |

---

## 5 המלצות לשיפור

1. **Data flow tracing** — בכל שינוי input, לעקוב עד ה-consumer
2. **Atomicity audit** — בכל לולאה עם side effects: "מה קורה אם נעצרים באמצע?"
3. **Safety net removal check** — כשמסירים מנגנון: "מה מחליף אותו?"
4. **Boundary table** — לפני לוגיקת זמן: טבלת תרחישים
5. **Constraint-first thinking** — בכל טבלה חדשה: "מה ה-natural key? יש UNIQUE?"
