import logging
import os
from datetime import datetime

import psycopg2
from openpyxl import Workbook

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

TOKEN = os.getenv("TOKEN")
OWNER_ID = 512147377

logging.basicConfig(level=logging.INFO)


# ---------------- DATABASE ----------------

def db():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def init_db():

    conn = db()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users(
        tg_id BIGINT PRIMARY KEY,
        name TEXT,
        role TEXT DEFAULT 'user'
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS categories(
        id SERIAL PRIMARY KEY,
        name TEXT UNIQUE,
        sort_order INTEGER DEFAULT 0
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS items(
        id SERIAL PRIMARY KEY,
        name TEXT,
        category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
        qty INTEGER DEFAULT 0,
        minimum INTEGER DEFAULT 0
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS history(
        id SERIAL PRIMARY KEY,
        item_id INTEGER,
        qty INTEGER,
        action TEXT,
        user_name TEXT,
        date TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS purchase(
        id SERIAL PRIMARY KEY,
        name TEXT
    )
    """)

    default_categories = [
        ("Расходники", 1),
        ("Материал", 2),
        ("Инструмент", 3),
        ("Металл", 4),
    ]

    for name, order in default_categories:
        c.execute("""
        INSERT INTO categories(name,sort_order)
        VALUES(%s,%s)
        ON CONFLICT(name) DO NOTHING
        """, (name, order))

    conn.commit()
    conn.close()


# ---------------- ROLES ----------------

def is_admin(uid):

    if uid == OWNER_ID:
        return True

    conn = db()
    c = conn.cursor()

    c.execute("SELECT role FROM users WHERE tg_id=%s", (uid,))
    r = c.fetchone()

    conn.close()

    return r and r[0] == "admin"


# ---------------- KEYBOARD ----------------

def main_kb(uid):

    kb = [
        ["📦 В наличии", "📋 Нужно заказать"],
        ["📊 Excel отчет"]
    ]

    if is_admin(uid):
        kb.insert(1, ["📜 Общая история"])

    return ReplyKeyboardMarkup(kb, resize_keyboard=True)


# ---------------- START ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    conn = db()
    c = conn.cursor()

    c.execute("""
    INSERT INTO users(tg_id,name,role)
    VALUES(%s,%s,%s)
    ON CONFLICT(tg_id) DO NOTHING
    """, (
        user.id,
        user.full_name,
        "admin" if user.id == OWNER_ID else "user"
    ))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "Главное меню",
        reply_markup=main_kb(user.id)
    )


# ---------------- CATEGORIES ----------------

async def categories(update: Update, context):

    conn = db()
    c = conn.cursor()

    c.execute("""
    SELECT id,name
    FROM categories
    ORDER BY sort_order
    """)

    rows = c.fetchall()
    conn.close()

    kb = []

    for r in rows:
        kb.append([
            InlineKeyboardButton(r[1], callback_data=f"cat_{r[0]}")
        ])

    kb.append([
        InlineKeyboardButton("➕ Добавить категорию", callback_data="add_category")
    ])

    await update.message.reply_text(
        "Категории",
        reply_markup=InlineKeyboardMarkup(kb)
    )


# ---------------- OPEN CATEGORY ----------------

async def open_category(update: Update, context):

    query = update.callback_query
    await query.answer()

    cat_id = int(query.data.split("_")[1])

    conn = db()
    c = conn.cursor()

    c.execute("""
    SELECT id,name,qty
    FROM items
    WHERE category_id=%s
    ORDER BY name
    """, (cat_id,))

    rows = c.fetchall()
    conn.close()

    kb = []

    for r in rows:
        kb.append([
            InlineKeyboardButton(
                f"{r[1]} ({r[2]})",
                callback_data=f"item_{r[0]}"
            )
        ])

    kb.append([
        InlineKeyboardButton("➕ Добавить позицию", callback_data=f"additem_{cat_id}")
    ])

    kb.append([
        InlineKeyboardButton("⬅ Назад", callback_data="back_categories")
    ])

    await query.message.edit_text(
        "Позиции категории",
        reply_markup=InlineKeyboardMarkup(kb)
    )


# ---------------- NEED ----------------

async def need(update: Update, context):

    conn = db()
    c = conn.cursor()

    c.execute("""
    SELECT name,qty
    FROM items
    WHERE qty<=minimum
    """)

    rows = c.fetchall()

    text = "⚠ Нужно пополнить\n\n"

    if rows:
        for r in rows:
            text += f"{r[0]} ({r[1]})\n"
    else:
        text += "Нет позиций\n"

    c.execute("SELECT name FROM purchase")
    p = c.fetchall()

    text += "\n🛒 Закупка\n"

    for i in p:
        text += f"{i[0]}\n"

    conn.close()

    kb = [
        [InlineKeyboardButton("➕ Добавить", callback_data="add_purchase")]
    ]

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(kb)
    )


# ---------------- EXCEL ----------------

async def excel(update: Update, context):

    conn = db()
    c = conn.cursor()

    c.execute("SELECT name,qty FROM items")
    items = c.fetchall()

    c.execute("SELECT name FROM purchase")
    purchase = c.fetchall()

    wb = Workbook()

    ws = wb.active
    ws.title = "Остаток"

    ws.append(["Название", "Количество"])

    for i in items:
        ws.append(i)

    ws2 = wb.create_sheet("Закупка")

    for p in purchase:
        ws2.append(p)

    file = "report.xlsx"
    wb.save(file)

    with open(file, "rb") as f:
        await update.message.reply_document(f)


# ---------------- ROUTER ----------------

async def msg_router(update: Update, context):

    text = update.message.text

    if text == "📦 В наличии":
        await categories(update, context)

    elif text == "📋 Нужно заказать":
        await need(update, context)

    elif text == "📊 Excel отчет":
        await excel(update, context)


async def cb_router(update: Update, context):

    data = update.callback_query.data

    if data.startswith("cat_"):
        await open_category(update, context)

    if data == "back_categories":
        await categories(update, context)


# ---------------- MAIN ----------------

def main():

    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(MessageHandler(filters.TEXT, msg_router))
    app.add_handler(CallbackQueryHandler(cb_router))

    print("BOT STARTED")

    app.run_polling()


if __name__ == "__main__":
    main()
