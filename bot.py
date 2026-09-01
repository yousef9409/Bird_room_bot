import os
import sqlite3
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)

# 1. إعداد قاعدة البيانات الموسعة
def init_db():
    conn = sqlite3.connect("birds_room.db")
    cursor = conn.cursor()
    # جدول الأقفاص
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cages (
            cage_number INTEGER PRIMARY KEY,
            male_id TEXT,
            female_id TEXT,
            strain TEXT,
            notes TEXT
        )
    ''')
    # جدول البطون والبيض
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clutches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cage_number INTEGER,
            eggs_count INTEGER,
            fertile_count INTEGER,
            lay_date TEXT,
            hatch_date TEXT,
            notes TEXT
        )
    ''')
    # جدول النظامات والوجبات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS nutrition (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day_or_stage TEXT,
            diet_details TEXT,
            supplements TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# 2. أدوات التعامل مع قاعدة البيانات
def save_cage(cage_number: int, male_id: str = "", female_id: str = "", strain: str = "", notes: str = ""):
    """حفظ أو تحديث بيانات قفص (الرقم، الذكر، الأنثى، السلالة مثل موزاييك/أجات، وملاحظات)."""
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
    ''', (cage_number, male_id, female_id, strain, notes))
    conn.commit()
    conn.close()
    return f"تم تسجيل/تحديث بيانات القفص رقم {cage_number} بنجاح."

def log_clutch(cage_number: int, eggs_count: int = 0, fertile_count: int = 0, lay_date: str = "", notes: str = ""):
    """تسجيل بطن بيض جديد لقفص معين (عدد البيض، المخصب، تاريخ البيض)."""
    conn = sqlite3.connect("birds_room.db")
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO clutches (cage_number, eggs_count, fertile_count, lay_date, notes)
        VALUES (?, ?, ?, ?, ?)
    ''', (cage_number, eggs_count, fertile_count, lay_date, notes))
    conn.commit()
    conn.close()
    return f"تم تسجيل بطن البيض للقفص رقم {cage_number} بنجاح."

def get_room_summary():
    """استرجاع تقرير شامل عن كافة الأقفاص والبطون المسجلة في غرفة الطيور."""
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

# 3. إدارة جلسات الحوار والذاكرة (Chat History)
user_chats = {}

SYSTEM_INSTRUCTION = """
أنت خبير ومساعد محترف في إدارة غرفة الطيور (خاصة الكناري والسلالات المختلفة مثل الموزاييك والأجات والتوباز والجينات والتغذية).
تتحدث بمرونة، ذكاء، وود كأنك زميل خبير ومساعد شخصي للمربي.

مهامك:
1. فهم كلام المربي وتحليله بدقة.
2. استخدام الأدوات المتاحة لترسيخ وحفظ البيانات (أقفاص، طفرات، بطون بيض، تواريخ) أو استرجاع التقرير الشامل.
3. الإجابة عن الاستفسارات وتوفير النصائح التخصصية في التغذية (مثل الخلطات والباتيه والمكملات كـ Ferti-Vit و AD3E) والتربية بأسلوب مرن وطبيعي جداً بدون رسميات جافة.
"""

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "يا مرحباً بك في نظام إدارة غرفة الطيور المطور! 🐦✨\n\n"
        "أنا جاهز لمساعدتك وتسجيل كل تفاصيل الأقفاص، الطفرات، البيض، والبرامج الغذائية.\n"
        "تحدث معي بشكل طبيعي كأنك تسولف مع خبير في غرفتك، مثلاً:\n"
        "• 'سجل قفص 1 فيه ذكر موزاييك أجات وأنثى موزاييك أحمر'\n"
        "• 'قفص 1 باضوا اليوم 4 بيضات'\n"
        "• 'اعرض لي التقرير الشامل للغرفة'\n"
        "• 'شو الأغذية المناسبة في مرحلة تجهيز الأزواج؟'"
    )
    await update.message.reply_text(welcome_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    # إنشاء جلسة شات للذاكرة لكل مستخدم
    if user_id not in user_chats:
        user_chats[user_id] = client.chats.create(
            model='gemini-2.5-flash',
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=[save_cage, log_clutch, get_room_summary],
                temperature=0.4
            )
        )
    
    chat = user_chats[user_id]

    try:
        response = chat.send_message(user_text)

        if response.function_calls:
            for call in response.function_calls:
                name = call.name
                args = call.args
                
                if name == "save_cage":
                    res = save_cage(**args)
                elif name == "log_clutch":
                    res = log_clutch(**args)
                elif name == "get_room_summary":
                    res = get_room_summary()
                else:
                    res = "أداة غير معروفة."

                # إرجاع نتيجة الأداة للنموذج للاستمرار في المحادثة
                second_response = chat.send_message(
                    types.Part.from_function_response(name=name, response={"result": res})
                )
                await update.message.reply_text(second_response.text)
                return

        if response.text:
            await update.message.reply_text(response.text)

    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text("حصل خطأ بسيط في معالجة الرسالة، جرب مرة ثانية.")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("البوت المطور يعمل الآن...")
    app.run_polling()
