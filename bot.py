import os
import sqlite3
import threading
import asyncio
from datetime import datetime, timedelta
from typing import Optional

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

genai.configure(api_key=GEMINI_API_KEY)

# جلسات محادثة Gemini للحيادية واستمرار السياق
user_sessions = {}
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
    """تسجيل أو تحديث بيانات قفص في قاعدة البيانات."""
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
    """إضافة أو تحديث طائر في سجلات غرفة الإنتاج."""
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
    """تسجيل بطنة بيض جديدة واحتساب تاريخ الفقس المتوقع بناءً على بداية الحضانة."""
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
    """تسجيل حالة صحية أو علاج لطائر معين."""
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
    """جلب تقرير ملخص شامل عن غرفة الطيور والإنتاج."""
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
    """عرض البطنات المتوقع فقسها خلال الأيام المقبلة."""
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
# تهيئة نموذج Gemini
# --------------------------
SYSTEM_PROMPT = """
أنت خبير ومستشار محترف في تربية الكناري وطفراته (مثل الموزاييك، الأجات، والتوباز)، وعلم الجينات، والحضانة، والتغذية، والعلاج.
تتحدث بأسلوب ودود وخبير، وتستخدم الأدوات المتاحة تلقائياً لتسجيل واسترجاع بيانات غرفة الإنتاج في قاعدة البيانات.
"""

model = genai.GenerativeModel(
    model_name='gemini-2.0-flash',
    system_instruction=SYSTEM_PROMPT,
    tools=[save_cage, add_bird, log_clutch, log_health, get_room_summary, get_upcoming_events]
)

# --------------------------
# معالجات تلغرام
# --------------------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("مرحباً بك في Canary Assistant Bot! 🐦✨\nأنا جاهز لمساعدتك في إدارة الأقفاص، الحجل، السلالات، ومواعيد الفقس.")

async def report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_room_summary())

async def upcoming_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_upcoming_events(7))

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text or ""

    if user_id not in user_sessions:
        user_sessions[user_id] = model.start_chat(enable_automatic_function_calling=True)

    chat = user_sessions[user_id]

    try:
        response = await asyncio.to_thread(chat.send_message, user_text)
        if response.text:
            await update.message.reply_text(response.text)
        else:
            await update.message.reply_text("تم تنفيذ الطلب بنجاح.")
    except Exception as e:
        user_sessions[user_id] = model.start_chat(enable_automatic_function_calling=True)
        try:
            response = await asyncio.to_thread(user_sessions[user_id].send_message, user_text)
            await update.message.reply_text(response.text)
        except Exception as err:
            await update.message.reply_text(f"⚠️ حدث خطأ أثناء المعالجة: {str(err)}")

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
