import sqlite3
import logging
from datetime import datetime
import os

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
    ContextTypes,
    filters,
    ConversationHandler,
)

TOKEN = os.getenv("TOKEN")
OWNER_ID = 512147377
DB_NAME = "warehouse.db"

logging.basicConfig(level=logging.INFO)

# STATES

ADD_CATEGORY = 1

ADD_ITEM_NAME = 10
ADD_ITEM_QTY = 11
ADD_ITEM_MIN = 12

CHANGE_QTY = 20

# DATABASE

def db():
    return sqlite3.connect(DB_NAME)


def init_db():

    conn = db()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users(
        tg_id INTEGER PRIMARY KEY,
        name TEXT,
        role TEXT DEFAULT 'user'
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS categories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        category_id INTEGER,
        qty INTEGER DEFAULT 0,
        minimum INTEGER DEFAULT 0
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER,
        qty INTEGER,
        action TEXT,
        user TEXT,
        date TEXT
    )
    """)

    for cat in ["Расходники", "Материал", "Инструмент", "Металл"]:
        c.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (cat,))

    conn.commit()
    conn.close()

# ROLE

def is_admin(uid):

    if uid == OWNER_ID:
        return True

    conn = db()
    c = conn.cursor()

    c.execute("SELECT role FROM users WHERE tg_id=?", (uid,))
    r = c.fetchone()

    conn.close()

    return r and r[0] == "admin"

# KEYBOARDS

def main_kb(uid):

    kb = [
        ["📦 В наличии", "📋 Нужно заказать"],
        ["📊 Excel отчет"]
    ]

    if is_admin(uid):
        kb.insert(1, ["📜 Общая история"])

    return ReplyKeyboardMarkup(kb, resize_keyboard=True)


# START

async def start(update: Update, context):

    user = update.effective_user

    conn = db()
    c = conn.cursor()

    c.execute(
        "INSERT OR IGNORE INTO users(tg_id,name,role) VALUES(?,?,?)",
        (
            user.id,
            user.full_name,
            "admin" if user.id == OWNER_ID else "user",
        ),
    )

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "Главное меню",
        reply_markup=main_kb(user.id),
    )

# CATEGORIES

async def categories(update: Update, context):

    conn = db()
    c = conn.cursor()

    c.execute("SELECT id,name FROM categories")
    rows = c.fetchall()

    conn.close()

    kb = []

    for r in rows:
        kb.append([
            InlineKeyboardButton(
                r[1],
                callback_data=f"cat_{r[0]}"
            )
        ])

    kb.append([
        InlineKeyboardButton(
            "➕ Добавить категорию",
            callback_data="add_category"
        )
    ])

    await update.message.reply_text(
        "Категории:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ADD CATEGORY

async def add_category_start(update, context):

    await update.callback_query.answer()

    await update.callback_query.message.reply_text(
        "Введите название категории:"
    )

    return ADD_CATEGORY


async def add_category_save(update, context):

    conn = db()
    c = conn.cursor()

    c.execute(
        "INSERT OR IGNORE INTO categories(name) VALUES(?)",
        (update.message.text,),
    )

    conn.commit()
    conn.close()

    await update.message.reply_text("Категория добавлена")

    return ConversationHandler.END

# SHOW ITEMS

async def show_items(update, context):

    query = update.callback_query
    await query.answer()

    cat = int(query.data.split("_")[1])

    context.user_data["cat"] = cat

    conn = db()
    c = conn.cursor()

    c.execute(
        "SELECT id,name,qty,minimum FROM items WHERE category_id=?",
        (cat,)
    )

    rows = c.fetchall()
    conn.close()

    kb = []

    for r in rows:

        status = "⚠" if r[2] <= r[3] else "✅"

        kb.append([
            InlineKeyboardButton(
                f"{r[1]} ({r[2]}) {status}",
                callback_data=f"item_{r[0]}"
            )
        ])

    kb.append([
        InlineKeyboardButton(
            "➕ Добавить позицию",
            callback_data="add_item"
        )
    ])

    await query.message.reply_text(
        "Позиции:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ADD ITEM

async def add_item_start(update, context):

    await update.callback_query.answer()

    await update.callback_query.message.reply_text(
        "Название позиции:"
    )

    return ADD_ITEM_NAME


async def add_item_name(update, context):

    context.user_data["name"] = update.message.text

    await update.message.reply_text("Количество:")

    return ADD_ITEM_QTY


async def add_item_qty(update, context):

    context.user_data["qty"] = int(update.message.text)

    await update.message.reply_text("Минимум:")

    return ADD_ITEM_MIN


async def add_item_min(update, context):

    conn = db()
    c = conn.cursor()

    c.execute(
        "INSERT INTO items(name,category_id,qty,minimum) VALUES(?,?,?,?)",
        (
            context.user_data["name"],
            context.user_data["cat"],
            context.user_data["qty"],
            int(update.message.text),
        ),
    )

    conn.commit()
    conn.close()

    await update.message.reply_text("Позиция добавлена")

    return ConversationHandler.END

# ITEM MENU

async def item_menu(update, context):

    query = update.callback_query
    await query.answer()

    context.user_data["item"] = int(query.data.split("_")[1])

    kb = [

        [
            InlineKeyboardButton("➕ Добавить", callback_data="plus"),
            InlineKeyboardButton("➖ Взять", callback_data="minus"),
        ],

        [
            InlineKeyboardButton("📜 История", callback_data="history")
        ]

    ]

    await query.message.reply_text(
        "Выберите действие:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# CHANGE

async def plus(update, context):

    await update.callback_query.answer()

    context.user_data["mode"] = "plus"

    await update.callback_query.message.reply_text(
        "Введите количество:"
    )

    return CHANGE_QTY


async def minus(update, context):

    await update.callback_query.answer()

    context.user_data["mode"] = "minus"

    await update.callback_query.message.reply_text(
        "Введите количество:"
    )

    return CHANGE_QTY


async def change_save(update, context):

    qty = int(update.message.text)

    if context.user_data["mode"] == "minus":
        qty = -qty

    conn = db()
    c = conn.cursor()

    c.execute(
        "UPDATE items SET qty=qty+? WHERE id=?",
        (qty, context.user_data["item"])
    )

    c.execute(
        """
        INSERT INTO history(item_id,qty,action,user,date)
        VALUES(?,?,?,?,?)
        """,
        (
            context.user_data["item"],
            qty,
            context.user_data["mode"],
            update.effective_user.full_name,
            datetime.now().strftime("%Y-%m-%d %H:%M")
        )
    )

    conn.commit()
    conn.close()

    await update.message.reply_text("Готово")

    return ConversationHandler.END

# HISTORY

async def history(update, context):

    conn = db()
    c = conn.cursor()

    c.execute("""
        SELECT items.name,
               history.qty,
               history.user,
               history.date
        FROM history
        JOIN items ON items.id=history.item_id
        ORDER BY history.date DESC
        LIMIT 50
    """)

    rows = c.fetchall()
    conn.close()

    text = "\n".join(
        f"{r[0]}  {r[1]}  {r[2]}  {r[3]}"
        for r in rows
    )

    if update.callback_query:

        await update.callback_query.message.reply_text(
            text or "История пуста"
        )

    else:

        await update.message.reply_text(
            text or "История пуста"
        )

# NEED

async def need(update, context):

    conn = db()
    c = conn.cursor()

    c.execute(
        "SELECT name,qty FROM items WHERE qty<=minimum"
    )

    rows = c.fetchall()

    conn.close()

    text = "\n".join(
        f"{r[0]} ({r[1]})"
        for r in rows
    )

    await update.message.reply_text(
        text or "Нет позиций"
    )

# EXCEL

async def excel(update, context):
conn = db()
cursor = conn.cursor()

# Получаем остатки
cursor.execute("SELECT name, qty FROM items")
items = cursor.fetchall()

# Получаем историю
cursor.execute("""
SELECT items.name,
history.qty,
history.user,
history.date
FROM history
JOIN items ON items.id = history.item_id
""")
history_data = cursor.fetchall()

# Получаем список "Нужно заказать"
cursor.execute("SELECT name, qty FROM items WHERE qty <= minimum")
need_data = cursor.fetchall()

conn.close()

wb = Workbook()

# Лист Остаток
ws1 = wb.active
ws1.title = "Остаток"
ws1.append(["Название позиции", "Количество"])
for row in items:
ws1.append(row)

# Лист История
ws2 = wb.create_sheet("История")
ws2.append(["Название позиции", "Количество", "Кто", "Когда"])
for row in history_data:
ws2.append(row)

# Лист Нужно заказать
ws3 = wb.create_sheet("Нужно заказать")
ws3.append(["Название позиции", "Остаток"])
for row in need_data:
ws3.append(row)

file_path = "report.xlsx"
wb.save(file_path)

await update.message.reply_document(
open(file_path, "rb")
)

# ROUTERS

async def msg_router(update, context):

    text = update.message.text

    if text == "📦 В наличии":
        await categories(update, context)

    elif text == "📋 Нужно заказать":
        await need(update, context)

    elif text == "📜 Общая история":
        await update.message.reply_text("История:")
        await history(update, context)

    elif text == "📊 Excel отчет":
        await excel(update, context)


async def cb_router(update, context):

    data = update.callback_query.data

    if data.startswith("cat_"):
        await show_items(update, context)

    elif data.startswith("item_"):
        await item_menu(update, context)

    elif data == "add_category":
        return await add_category_start(update, context)

    elif data == "add_item":
        return await add_item_start(update, context)

    elif data == "plus":
        return await plus(update, context)

    elif data == "minus":
        return await minus(update, context)

    elif data == "history":
        return await history(update, context)

# MAIN

def main():

    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))


    # СНАЧАЛА ConversationHandlers

    app.add_handler(
        ConversationHandler(
            entry_points=[
                CallbackQueryHandler(add_category_start, pattern="add_category")
            ],
            states={
                ADD_CATEGORY: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, add_category_save)
                ]
            },
            fallbacks=[]
        )
    )


    app.add_handler(
        ConversationHandler(
            entry_points=[
                CallbackQueryHandler(add_item_start, pattern="add_item")
            ],
            states={
                ADD_ITEM_NAME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, add_item_name)
                ],
                ADD_ITEM_QTY: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, add_item_qty)
                ],
                ADD_ITEM_MIN: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, add_item_min)
                ],
            },
            fallbacks=[]
        )
    )


    app.add_handler(
        ConversationHandler(
            entry_points=[
                CallbackQueryHandler(plus, pattern="plus"),
                CallbackQueryHandler(minus, pattern="minus")
            ],
            states={
                CHANGE_QTY: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, change_save)
                ]
            },
            fallbacks=[]
        )
    )


    # ПОСЛЕ ConversationHandlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_router))

    app.add_handler(CallbackQueryHandler(cb_router))

    print("BOT STARTED")

    app.run_polling()


if __name__ == "__main__":
    main()
