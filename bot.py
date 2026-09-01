import os
import sqlite3
import logging
import threading
import asyncio
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# إعداد مكتبة Gemini
genai.configure(api_key=GEMINI_API_KEY)

# --- 1. خادم Flask الخفيف لبيئة Render ---
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bird Room Bot is Active!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port)

# --- 2. إدارة قاعدة البيانات SQLite ---
def init_db():
    conn = sqlite3.connect("birds_room.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cages (
            cage_number INTEGER PRIMARY KEY,
            male_id TEXT,
            female_id TEXT,
            strain TEXT,
            notes TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clutches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cage_number INTEGER,
            eggs_count INTEGER,
            fertile_count INTEGER,
            lay_date TEXT,
            notes TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- 3. أدوات التحكم بالقاعدة ---
def save_cage(cage_number: int, male_id: str = "", female_id: str = "", strain: str = "", notes: str = ""):
    """تسجيل أو تحديث البيانات الخاصة بقفص مع معين (رقم القفص، الذكر، الأنثى، الطفرة/السلالة، ملاحظات)."""
    conn = sqlite3.connect("birds_room.db")
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO cages (cage_number, male_id, female_id, strain, notes)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(cage_number) DO UPDATE SET
            male_id=COALESCE(NULLIF(excluded.male_id, ''), male_id),
            female_id=COALESCE(NULLIF(excluded.female_id, ''), female_id),
            strain=COALESCE(NULLIF(excluded.strain, ''), strain),
            notes=COALESCE(NULLIF(excluded.notes, ''), notes)
    ''', (cage_number, str(male_id), str(female_id), str(strain), str(notes)))
    conn.commit()
    conn.close()
    return f"تم تسجيل البيانات للقفص رقم {cage_number} بنجاح في قاعدة البيانات."

def log_clutch(cage_number: int, eggs_count: int = 0, fertile_count: int = 0, lay_date: str = "", notes: str = ""):
    """تسجيل بطن بيض جديد لقفص معين (عدد البيض، البيض المخصب، تاريخ البياض، ملاحظات)."""
    conn = sqlite3.connect("birds_room.db")
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO clutches (cage_number, eggs_count, fertile_count, lay_date, notes)
        VALUES (?, ?, ?, ?, ?)
    ''', (cage_number, eggs_count, fertile_count, str(lay_date), str(notes)))
    conn.commit()
    conn.close()
    return f"تم تسجيل بطن البيض للقفص رقم {cage_number} بنجاح."

def get_room_summary():
    """استرجاع تقرير شامل لجميع الأقفاص والمحتويات وبطون البيض في غرفة الطيور."""
    conn = sqlite3.connect("birds_room.db")
    cursor = conn.cursor()
    cursor.execute("SELECT cage_number, male_id, female_id, strain, notes FROM cages")
    cages = cursor.fetchall()
    
    if not cages:
        conn.close()
        return "غرفة الطيور فارغة حالياً، لم يتم تسجيل أية أقفاص بعد."
    
    report = "📊 **تقرير غرفة الطيور:**\n\n"
    for c in cages:
        report += f"🔹 **قفص {c[0]}** | السلالة: {c[3] or 'غير محددة'}\n"
        report += f"   - الذكر: {c[1] or 'غير محدد'} | الأنثى: {c[2] or 'غير محدد'}\n"
        if c[4]:
            report += f"   - ملاحظات: {c[4]}\n"
            
        cursor.execute("SELECT eggs_count, fertile_count, lay_date FROM clutches WHERE cage_number = ?", (c[0],))
        clutches = cursor.fetchall()
        if clutches:
            for idx, cl in enumerate(clutches, 1):
                report += f"   🐣 *بطن {idx}:* {cl[0]} بيضات (المخصب: {cl[1]}) | تاريخ: {cl[2] or 'غير محدد'}\n"
        report += "---------------------\n"
    
    conn.close()
    return report

# --- 4. إعداد النموذج والشخصية ---
SYSTEM_INSTRUCTION = """
أنت خبير ومساعد محترف في إدارة غرفة الطيور (خاصة الكناري والسلالات المختلفة مثل الموزاييك والأجات والتوباز وأوبال والجينات والتغذية).
تتحدث بمرونة، ذكاء، وود كأنك زميل خبير ومساعد شخصي للمربي.

عندما ينطلب منك حفظ أو تسجيل أي قفص، طفرة، بيض، أو عرض التقرير، استخدم الدوال (Tools) المتاحة لك فوراً.
"""

model = genai.GenerativeModel(
    model_name='gemini-1.5',
    system_instruction=SYSTEM_INSTRUCTION,
    tools=[save_cage, log_clutch, get_room_summary]
)


# حفظ جلسات الشات بشكل آمن
user_sessions = {}

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "يا مرحباً بك في نظام إدارة غرفة الطيور المطور! 🐦✨\n\n"
        "أنا جاهز لمساعدتك وتسجيل كل تفاصيل الأقفاص، الطفرات، البيض، والبرامج الغذائية.\n"
        "تحدث معي بشكل طبيعي كأنك تسولف مع خبير في غرفتك."
    )
    await update.message.reply_text(welcome_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    if user_id not in user_sessions:
        user_sessions[user_id] = model.start_chat(enable_automatic_function_calling=True)
    
    chat = user_sessions[user_id]

    try:
        # تشغيل طلب Gemini داخل Thread منفصل لمنع تجميد تلغرام
        response = await asyncio.to_thread(chat.send_message, user_text)
        
        if response.text:
            await update.message.reply_text(response.text)
        else:
            await update.message.reply_text("تم حفظ البيانات بنجاح في النظام.")

    except Exception as e:
        logging.error(f"Error handling message: {e}")
        # إعادة فتح الجلسة تلقائياً في حال انتهت
        user_sessions[user_id] = model.start_chat(enable_automatic_function_calling=True)
        try:
            response = await asyncio.to_thread(user_sessions[user_id].send_message, user_text)
            await update.message.reply_text(response.text)
        except Exception as err:
            await update.message.reply_text(f"⚠️ حدث خطأ في النظام: {str(err)}")

if __name__ == '__main__':
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

    app_tg = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app_tg.add_handler(CommandHandler("start", start_command))
    app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("البوت يعمل الآن بصورة مستقرة...")
    app_tg.run_polling(drop_pending_updates=True)
