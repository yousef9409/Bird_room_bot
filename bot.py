import os
import sqlite3
import logging
import threading
import asyncio
from datetime import datetime, timedelta
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

# ------------------- Flask لـ Render -------------------
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bird Room Production Assistant is Active! 🐦", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port)

# ------------------- قاعدة البيانات الموسعة -------------------
def init_db():
    conn = sqlite3.connect("birds_room.db")
    cursor = conn.cursor()
    
    # الأقفاص
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cages (
            cage_number INTEGER PRIMARY KEY,
            location TEXT,
            notes TEXT
        )
    ''')
    
    # الطيور الفردية
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS birds (
            bird_id TEXT PRIMARY KEY,
            ring_number TEXT,
            gender TEXT,
            strain TEXT,
            mutation TEXT,
            birth_date TEXT,
            father_id TEXT,
            mother_id TEXT,
            cage_number INTEGER,
            status TEXT DEFAULT 'نشط',
            notes TEXT
        )
    ''')
    
    # بطون البيض / الحضانة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clutches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cage_number INTEGER,
            pair_male TEXT,
            pair_female TEXT,
            eggs_count INTEGER,
            fertile_count INTEGER,
            lay_date TEXT,
            incubation_start TEXT,
            expected_hatch TEXT,
            hatched_count INTEGER DEFAULT 0,
            notes TEXT
        )
    ''')
    
    # السجلات الصحية
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS health_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bird_id TEXT,
            date TEXT,
            issue TEXT,
            treatment TEXT,
            notes TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ------------------- أدوات قاعدة البيانات (Tools) -------------------
def save_cage(cage_number: int, location: str = "", notes: str = ""):
    """تسجيل أو تحديث بيانات قفص."""
    conn = sqlite3.connect("birds_room.db")
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO cages (cage_number, location, notes)
        VALUES (?, ?, ?)
        ON CONFLICT(cage_number) DO UPDATE SET
            location = COALESCE(NULLIF(excluded.location, ''), location),
            notes = COALESCE(NULLIF(excluded.notes, ''), notes)
    ''', (cage_number, location, notes))
    conn.commit()
    conn.close()
    return f"✅ تم حفظ بيانات القفص رقم {cage_number} بنجاح."

def add_bird(bird_id: str, ring_number: str = "", gender: str = "", strain: str = "", mutation: str = "", birth_date: str = "", father_id: str = "", mother_id: str = "", cage_number: int = 0, notes: str = ""):
    """إضافة أو تحديث طائر جديد في النظام."""
    conn = sqlite3.connect("birds_room.db")
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO birds (bird_id, ring_number, gender, strain, mutation, birth_date, father_id, mother_id, cage_number, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bird_id) DO UPDATE SET
            ring_number = COALESCE(NULLIF(excluded.ring_number, ''), ring_number),
            gender = COALESCE(NULLIF(excluded.gender, ''), gender),
            strain = COALESCE(NULLIF(excluded.strain, ''), strain),
            mutation = COALESCE(NULLIF(excluded.mutation, ''), mutation),
            birth_date = COALESCE(NULLIF(excluded.birth_date, ''), birth_date),
            father_id = COALESCE(NULLIF(excluded.father_id, ''), father_id),
            mother_id = COALESCE(NULLIF(excluded.mother_id, ''), mother_id),
            cage_number = CASE WHEN excluded.cage_number != 0 THEN excluded.cage_number ELSE cage_number END,
            notes = COALESCE(NULLIF(excluded.notes, ''), notes)
    ''', (bird_id, ring_number, gender, strain, mutation, birth_date, father_id, mother_id, cage_number, notes))
    conn.commit()
    conn.close()
    return f"✅ تم تسجيل الطائر {bird_id} بنجاح."

def log_clutch(cage_number: int, pair_male: str = "", pair_female: str = "", eggs_count: int = 0, fertile_count: int = 0, lay_date: str = "", incubation_start: str = "", notes: str = ""):
    """تسجيل بطن بيض جديد مع حساب تاريخ الفقس المتوقع (13-14 يوم)."""
    expected_hatch = ""
    if incubation_start:
        try:
            start = datetime.strptime(incubation_start, "%Y-%m-%d")
            expected = start + timedelta(days=13)
            expected_hatch = expected.strftime("%Y-%m-%d")
        except Exception:
            pass

    conn = sqlite3.connect("birds_room.db")
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO clutches (cage_number, pair_male, pair_female, eggs_count, fertile_count, lay_date, incubation_start, expected_hatch, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (cage_number, pair_male, pair_female, eggs_count, fertile_count, lay_date, incubation_start, expected_hatch, notes))
    conn.commit()
    conn.close()
    return f"✅ تم تسجيل بطن البيض للقفص {cage_number}. تاريخ الفقس المتوقع: {expected_hatch or 'غير محدد'}."

def log_health_record(bird_id: str, issue: str, treatment: str = "", date: str = "", notes: str = ""):
    """تسجيل حالة صحية أو علاج لطائر."""
    conn = sqlite3.connect("birds_room.db")
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO health_records (bird_id, date, issue, treatment, notes)
        VALUES (?, ?, ?, ?, ?)
    ''', (bird_id, date or datetime.now().strftime("%Y-%m-%d"), issue, treatment, notes))
    conn.commit()
    conn.close()
    return f"✅ تم تسجيل الحالة الصحية للطائر {bird_id}."

def get_room_summary():
    """عرض تقرير شامل لغرفة الطيور."""
    conn = sqlite3.connect("birds_room.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT cage_number, location, notes FROM cages")
    cages = cursor.fetchall()
    
    if not cages:
        conn.close()
        return "غرفة الطيور فارغة حالياً ولم يتم تسجيل أقفاص."

    report = "📊 **تقرير غرفة الطيور الشامل:**\n\n"
    for cage in cages:
        c_num = cage[0]
        report += f"🏠 **القفص {c_num}** ({cage[1] or 'بدون موقع'})\n"
        
        cursor.execute("SELECT bird_id, gender, strain, mutation FROM birds WHERE cage_number = ?", (c_num,))
        birds = cursor.fetchall()
        if birds:
            for b in birds:
                report += f"   • طائر {b[0]} | الجنس: {b[1] or '-'} | السلالة: {b[2] or '-'} | الطفرة: {b[3] or '-'}\n"
        
        cursor.execute("SELECT eggs_count, fertile_count, expected_hatch FROM clutches WHERE cage_number = ? ORDER BY id DESC LIMIT 1", (c_num,))
        clutch = cursor.fetchone()
        if clutch:
            report += f"   🐣 *بطن قائم:* {clutch[0]} بيضات (مخصب: {clutch[1]}) |فقس متوقع: {clutch[2] or 'غير محدد'}\n"
            
        report += "---------------------\n"

    conn.close()
    return report

# ------------------- إعداد الموديل والشخصية -------------------
SYSTEM_INSTRUCTION = """
أنت خبير ومساعد محترف في إدارة غرفة الطيور وتتبع السلالات والطفرات والجينات (مثل الكناري الموزاييك، الأجات، والتوباز، والفينش).
تتحدث بمرونة وذكاء كزميل خبير.

استخدم الأدوات المتاحة (Tools) فوراً للقيام بالعمليات على قاعدة البيانات بناءً على طلبات المربي.
"""

model = genai.GenerativeModel(
    model_name='gemini-2.0-flash',
    system_instruction=SYSTEM_INSTRUCTION,
    tools=[save_cage, add_bird, log_clutch, log_health_record, get_room_summary]
)

user_sessions = {}

# ------------------- معالجات تلغرام -------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("يا مرحباً بك في نظام إدارة غرفة الطيور المطور! 🐦✨\nأنا جاهز لتسجيل الأقفاص، حجل الطيور، السلالات، والحالات الصحية.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    if user_id not in user_sessions:
        user_sessions[user_id] = model.start_chat(enable_automatic_function_calling=True)
    
    chat = user_sessions[user_id]

    try:
        response = await asyncio.to_thread(chat.send_message, user_text)
        if response.text:
            await update.message.reply_text(response.text)
        else:
            await update.message.reply_text("تمت العملية بنجاح.")

    except Exception as e:
        logging.error(f"Error handling message: {e}")
        user_sessions[user_id] = model.start_chat(enable_automatic_function_calling=True)
        try:
            response = await asyncio.to_thread(user_sessions[user_id].send_message, user_text)
            await update.message.reply_text(response.text)
        except Exception as err:
            await update.message.reply_text(f"⚠️ حدث خطأ أثناء المعالجة: {str(err)}")

if __name__ == '__main__':
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

    app_tg = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app_tg.add_handler(CommandHandler("start", start_command))
    app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("البوت يعمل بنجاح...")
    app_tg.run_polling(drop_pending_updates=True)
