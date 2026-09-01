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

def add_bird(bird_id: str, ring_number: str = "", gender: str = "", strain: str = "",
             mutation: str = "", birth_date: str = "", father_id: str = "",
             mother_id: str = "", cage_number: int = 0, notes: str = ""):
    """إضافة أو تحديث طائر جديد في النظام."""
    conn = sqlite3.connect("birds_room.db")
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO birds (bird_id, ring_number, gender, strain, mutation, birth_date,
                           father_id, mother_id, cage_number, notes)
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
    ''', (bird_id, ring_number, gender, strain, mutation, birth_date,
          father_id, mother_id, cage_number, notes))
    conn.commit()
    conn.close()
    return f"✅ تم تسجيل الطائر {bird_id} بنجاح."

def log_clutch(cage_number: int, pair_male: str = "", pair_female: str = "",
               eggs_count: int = 0, fertile_count: int = 0, lay_date: str = "",
               incubation_start: str = "", notes: str = ""):
    """تسجيل بطن بيض جديد مع حساب تاريخ الفقس المتوقع (13-14 يوم)."""
    expected_hatch = ""
    if incubation_start:
        try:
            start = datetime.strptime(incubation_start, "%Y-%m-%d")
            expected = start + timedelta(days=13)
            expected_hatch = expected.strftime("%Y-%m-%d")
        except:
            pass

    conn = sqlite3.connect("birds_room.db")
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO clutches (cage_number, pair_male,
