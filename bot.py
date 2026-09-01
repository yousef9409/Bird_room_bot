import os
import sqlite3
import threading
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict

from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import google.generativeai as genai

# --------------------------
# إعداد المتغيرات البيئية
# --------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
PORT = int(os.environ.get("PORT", "10000"))
DB_PATH = os.environ.get("CANARY_DB_PATH", "canary_room.db")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("يرجى تعيين متغير البيئة TELEGRAM_BOT_TOKEN")
if not GEMINI_API_KEY:
    raise RuntimeError("يرجى تعيين متغير البيئة GEMINI_API_KEY")

# Configure the google generative AI client
genai.configure(api_key=GEMINI_API_KEY)

# جلسات محادثة (نحتفظ بسجل مبسط من الرسائل لكل مستخدم)
user_sessions: Dict[int, List[Dict[str, str]]] = {}
DB_LOCK = threading.Lock()

# --------------------------
# قاعدة البيانات وإعداد الجداول
# --------------------------
def init_db() -> None:
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cages (
                cage_id TEXT PRIMARY KEY,
                location TEXT,
                notes TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS birds (
                bird_id TEXT PRIMARY KEY,
                ring_number TEXT,
                gender TEXT,
                breed TEXT,
                mutation TEXT,
                birth_date DATE,
                father_id TEXT,
                mother_id TEXT,
                cage_id TEXT,
                status TEXT,
                notes TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clutches (
                clutch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                cage_id TEXT,
                male_id TEXT,
                female_id TEXT,
                egg_count INTEGER,
                fertile_count INTEGER,
                lay_date DATE,
                incubation_start_date DATE,
                expected_hatch_date DATE,
                hatched_count INTEGER,
                notes TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS health_records (
                record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                bird_id TEXT,
                record_date DATE,
                issue TEXT,
                treatment TEXT,
                notes TEXT
            )
        """)
        conn.commit()
        conn.close()


def db_execute(query: str, params: tuple = (), fetch: bool = False):
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute(query, params)
        result = cur.fetchall() if fetch else None
        conn.commit()
        conn.close()
        return result

# --------------------------
# أدوات (Tools) التحكم بقاعدة البيانات
# --------------------------
def save_cage(cage_id: str, location: str = "", notes: str = "") -> str:
    db_execute(
        """
        INSERT INTO cages (cage_id, location, notes)
        VALUES (?, ?, ?)
        ON CONFLICT(cage_id) DO UPDATE SET location=excluded.location, notes=excluded.notes
        """,
        (cage_id, location, notes),
    )
    return f"تم حفظ القفص '{cage_id}' بنجاح. الموقع: '{location}'."


def add_bird(
    bird_id: str,
    ring_number: str = "",
    gender: str = "",
    breed: str = "",
    mutation: str = "",
    birth_date: str = "",
    father_id: str = "",
    mother_id: str = "",
    cage_id: str = "",
    status: str = "نشط",
    notes: str = "",
) -> str:
    db_execute(
        """
        INSERT INTO birds (bird_id, ring_number, gender, breed, mutation, birth_date, father_id, mother_id, cage_id, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bird_id) DO UPDATE SET
          ring_number=excluded.ring_number,
          gender=excluded.gender,
          breed=excluded.breed,
          mutation=excluded.mutation,
          birth_date=excluded.birth_date,
          father_id=excluded.father_id,
          mother_id=excluded.mother_id,
          cage_id=excluded.cage_id,
          status=excluded.status,
          notes=excluded.notes
        """,
        (bird_id, ring_number, gender, breed, mutation, birth_date, father_id, mother_id, cage_id, status, notes),
    )
    return f"تم إضافة/تحديث الطائر '{bird_id}' بنجاح (القفص: '{cage_id}')."


def log_clutch(
    cage_id: str,
    male_id: str = "",
    female_id: str = "",
    egg_count: int = 0,
    fertile_count: int = 0,
    lay_date: str = "",
    incubation_start_date: str = "",
    hatch_days: int = 13,
    hatched_count: int = 0,
    notes: str = "",
) -> str:
    expected_db = ""
    if incubation_start_date:
        try:
            start_dt = datetime.strptime(incubation_start_date, "%Y-%m-%d")
            expected_db = (start_dt + timedelta(days=hatch_days)).strftime("%Y-%m-%d")
        except Exception:
            pass

    db_execute(
        """
        INSERT INTO clutches (cage_id, male_id, female_id, egg_count, fertile_count, lay_date, incubation_start_date, expected_hatch_date, hatched_count, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (cage_id, male_id, female_id, egg_count, fertile_count, lay_date, incubation_start_date, expected_db, hatched_count, notes),
    )
    return f"تم تسجيل البطنة للقفص '{cage_id}'. الفقس المتوقع: '{expected_db or 'غير محدد'}'."


def log_health(bird_id: str, issue: str, treatment: str = "", notes: str = "") -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    db_execute(
        """
        INSERT INTO health_records (bird_id, record_date, issue, treatment, notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (bird_id, today, issue, treatment, notes),
    )
    return f"تم تسجيل حالة صحية للطائر '{bird_id}' بتاريخ {today}."


def get_room_summary() -> str:
    cages = db_execute("SELECT COUNT(*) FROM cages", fetch=True)[0][0]
    birds = db_execute("SELECT COUNT(*) FROM birds", fetch=True)[0][0]
    clutches_active = db_execute("SELECT COUNT(*) FROM clutches WHERE expected_hatch_date IS NOT NULL AND (hatched_count IS NULL OR hatched_count < egg_count)", fetch=True)[0][0]
    health_total = db_execute("SELECT COUNT(*) FROM health_records", fetch=True)[0][0]

    return (
        f"📊 **ملخص غرفة الإنتاج:**\n"
        f"- عدد الأقفاص: {cages}\n"
        f"- إجمالي الطيور: {birds}\n"
        f"- البطنات القائمة تحت الحضانة: {clutches_active}\n"
        f"- إجمالي السجلات الصحية: {health_total}\n"
    )


def get_upcoming_events(days_ahead: int = 7) -> str:
    today = datetime.now().date()
    end = today + timedelta(days=days_ahead)
    rows = db_execute("SELECT clutch_id, cage_id, egg_count, expected_hatch_date FROM clutches WHERE expected_hatch_date IS NOT NULL", fetch=True)
    
    upcoming = []
    for r in rows:
        try:
            e_date = datetime.strptime(r[3], "%Y-%m-%d").date()
            if today <= e_date <= end:
                upcoming.append(f"- بطنة #{r[0]} في القفص {r[1]} (بيض: {r[2]}) -> فقس متوقع: {r[3]}")
        except Exception:
            continue

    if not upcoming:
        return f"لا توجد أحداث فقس متوقعة خلال الـ {days_ahead} أيام القادمة."
    return f"🐣 **الأحداث القادمة خلال {days_ahead} أيام:**\n" + "\n".join(upcoming)

# --------------------------
# تهيئة نموذج Gemini (باستخدام واجهة generate_content)
# --------------------------
SYSTEM_PROMPT = """
أنت خبير ومستشار محترف في تربية الكناري وطفراته (مثل الموزاييك، الأجات، والتوباز)، وعلم الجينات، والحضانة، وعمليات التربية وإدارة الأقفاص.
تتحدث بأسلوب ودود وخبير، وتستخدم الأدوات المتاحة تلقائياً لتسجيل واسترجاع بيانات غرفة الإنتاج في قاعدة البيانات عندما يطلب المستخدم ذلك صراحةً.
"""

# استخدم النموذج الأحدث
model = genai.GenerativeModel("models/gemini-3.6-flash")

# Helper: تحويل سجل المحادثة إلى صيغة يقبلها SDK
def build_genai_messages(history: List[Dict[str, str]]) -> List[Dict[str, List[str]]]:
    msgs = []
    for m in history:
        role = m.get("role")
        text = m.get("text", "")
        if role == "system":
            msgs.append({"role": "system", "parts": [text]})
        elif role == "user":
            msgs.append({"role": "user", "parts": [text]})
        elif role == "assistant":
            msgs.append({"role": "assistant", "parts": [text]})
    return msgs

# --------------------------
# معالجات تلغرام
# --------------------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("مرحباً بك في Canary Assistant Bot! 🐦✨\nأنا جاهز لمساعدتك في إدارة الأقفاص، الطيور، والسجلات. جرّب الأوامر: /report أو /upcoming أو أرسل سؤالاً طبيعياً.")

async def report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_room_summary())

async def upcoming_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_upcoming_events(7))

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text or ""

    # تأكد من وجود سجل محادثة للمستخدم (نضع system prompt أولاً)
    if user_id not in user_sessions:
        user_sessions[user_id] = [{"role": "system", "text": SYSTEM_PROMPT}]

    history = user_sessions[user_id]
    history.append({"role": "user", "text": user_text})

    # حدّ من طول السجل (حافظ على الرسالة النظامية + آخر 20 رسالة)
    if len(history) > 22:
        history = [history[0]] + history[-21:]
        user_sessions[user_id] = history

    try:
        messages = build_genai_messages(history)
        # استدعاء نموذج Gemini للحصول على الرد
        response = await asyncio.to_thread(model.generate_content, messages)

        # استخراج النص من الاستجابة (عدة طرق بحسب إصدار المكتبة)
        reply_text = getattr(response, "text", None)
        if not reply_text:
            try:
                reply_text = response.output[0].content[0].text
            except Exception:
                reply_text = None

        if reply_text:
            history.append({"role": "assistant", "text": reply_text})
            # تحديث السجل المختصر
            if len(history) > 22:
                history = [history[0]] + history[-21:]
                user_sessions[user_id] = history

            await update.message.reply_text(reply_text)
        else:
            await update.message.reply_text("تم تنفيذ الطلب بنجاح.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ حدث خطأ أثناء المعالجة: {str(e)}")

# --------------------------
# Flask Web Server (Render Health Check)
# --------------------------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def index():
    return "Canary Assistant Bot is Active!", 200


def run_flask():
    app.run(host="0.0.0.0", port=PORT)

# --------------------------
# التشغيل الرئيسي
# --------------------------
if __name__ == "__main__":
    init_db()

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("report", report_handler))
    application.add_handler(CommandHandler("upcoming", upcoming_handler))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))

    print("تشغيل Canary Assistant Bot...")
    application.run_polling(drop_pending_updates=True)
