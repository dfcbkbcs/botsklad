import logging
from datetime import datetime
import os
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
    ConversationHandler,
)

TOKEN = os.getenv("TOKEN")
OWNER_ID = 512147377

logging.basicConfig(level=logging.INFO)

ADD_CATEGORY = 1
ADD_ITEM_NAME = 10
ADD_ITEM_QTY = 11
ADD_ITEM_MIN = 12
CHANGE_QTY = 20
ADD_PURCHASE = 30


# DATABASE

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
        name TEXT UNIQUE
    )
    """)

    c.execute("""
    ALTER TABLE categories
    ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0
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
        item_id INTEGER REFERENCES items(id) ON DELETE CASCADE,
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
        INSERT INTO categories(name, sort_order)
        VALUES(%s,%s)
        ON CONFLICT (name) DO NOTHING
        """, (name, order))

    conn.commit()
    conn.close()


# ROLE

def is_admin(uid):

    if uid == OWNER_ID:
        return True

    conn = db()
    c = conn.cursor()

    c.execute("SELECT role FROM users WHERE tg_id=%s", (uid,))
    r = c.fetchone()

    conn.close()

    return r and r[0] == "admin"


# KEYBOARD

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

    c.execute("""
    INSERT INTO users(tg_id,name,role)
    VALUES(%s,%s,%s)
    ON CONFLICT (tg_id) DO NOTHING
    """, (
        user.id,
        user.full_name,
        "admin" if user.id == OWNER_ID else "user",
    ))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "Главное меню",
        reply_markup=main_kb(user.id),
    )


# КАТЕГОРИИ

async def categories(update: Update, context):

    conn = db()
    c = conn.cursor()

    c.execute("""
    SELECT id,name
    FROM categories
    ORDER BY sort_order NULLS LAST, id
    """)

    rows = c.fetchall()
    conn.close()

    kb = []

    for r in rows:

        kb.append([
            InlineKeyboardButton(r[1], callback_data=f"cat_{r[0]}"),
            InlineKeyboardButton("❌", callback_data=f"del_cat_{r[0]}")
        ])

    kb.append([InlineKeyboardButton("➕ Добавить категорию", callback_data="add_category")])

    await update.message.reply_text(
        "Категории:",
        reply_markup=InlineKeyboardMarkup(kb)
    )


# ПОЗИЦИИ В КАТЕГОРИИ

async def show_items(update, context):

    query = update.callback_query
    await query.answer()

    cat = int(query.data.split("_")[1])
    context.user_data["cat"] = cat

    conn = db()
    c = conn.cursor()

    c.execute(
        "SELECT id,name,qty,minimum FROM items WHERE category_id=%s ORDER BY name",
        (cat,)
    )

    rows = c.fetchall()
    conn.close()

    kb = []

    for r in rows:

        status = "⚠️" if r[2] <= r[3] else "✅"

        kb.append([
            InlineKeyboardButton(
                f"{r[1]} ({r[2]}) {status}",
                callback_data=f"item_{r[0]}"
            ),
            InlineKeyboardButton(
                "❌",
                callback_data=f"del_item_{r[0]}"
            )
        ])

    kb.append([InlineKeyboardButton("➕ Добавить позицию", callback_data="add_item")])

    kb.append([InlineKeyboardButton("⬅ Назад", callback_data="back_categories")])

    await query.message.reply_text(
        "Позиции:",
        reply_markup=InlineKeyboardMarkup(kb)
    )


# ДОБАВЛЕНИЕ КАТЕГОРИИ

async def add_category_start(update, context):

    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Введите название категории:")
    return ADD_CATEGORY


async def add_category_save(update, context):

    conn = db()
    c = conn.cursor()

    c.execute("SELECT COALESCE(MAX(sort_order),0) FROM categories")
    max_order = c.fetchone()[0] + 1

    c.execute("""
    INSERT INTO categories(name, sort_order)
    VALUES(%s,%s)
    ON CONFLICT (name) DO NOTHING
    """, (update.message.text, max_order))

    conn.commit()
    conn.close()

    await update.message.reply_text("Категория добавлена")

    return ConversationHandler.END


# УДАЛЕНИЕ КАТЕГОРИИ

async def delete_category(update, context):

    query = update.callback_query
    await query.answer()

    cat_id = int(query.data.split("_")[2])

    conn = db()
    c = conn.cursor()

    c.execute("DELETE FROM categories WHERE id=%s", (cat_id,))
    conn.commit()
    conn.close()

    await query.message.reply_text("Категория удалена")


# УДАЛЕНИЕ ПОЗИЦИИ

async def delete_item(update, context):

    query = update.callback_query
    await query.answer()

    item_id = int(query.data.split("_")[2])

    conn = db()
    c = conn.cursor()

    c.execute("DELETE FROM items WHERE id=%s", (item_id,))
    conn.commit()
    conn.close()

    await query.message.reply_text("Позиция удалена")


# NEED

async def need(update: Update, context):

    conn = db()
    c = conn.cursor()

    c.execute("SELECT name,qty FROM items WHERE qty<=minimum ORDER BY name")
    low_items = c.fetchall()

    c.execute("SELECT id,name FROM purchase ORDER BY id DESC")
    purchase_items = c.fetchall()

    conn.close()

    text = "⚠ Нужно пополнить:\n"
    text += "\n".join(f"{r[0]} ({r[1]})" for r in low_items) or "Нет позиций"

    text += "\n\n🛒 Список закупки:\n"
    text += "\n".join(r[1] for r in purchase_items) or "Пусто"

    kb = [
        [InlineKeyboardButton("➕ Добавить в закупку", callback_data="add_purchase")]
    ]

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(kb)
    )


# ДОБАВИТЬ В ЗАКУПКУ

async def add_purchase_start(update, context):

    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Введите название позиции:")
    return ADD_PURCHASE


async def add_purchase_save(update, context):

    conn = db()
    c = conn.cursor()

    c.execute("INSERT INTO purchase(name) VALUES(%s)", (update.message.text,))

    conn.commit()
    conn.close()

    await update.message.reply_text("Добавлено в закупку")

    return ConversationHandler.END


# EXCEL

async def excel(update, context):

    conn = db()
    c = conn.cursor()

    c.execute("SELECT name, qty FROM items")
    items = c.fetchall()

    c.execute("""
    SELECT items.name, history.qty, history.user_name, history.date
    FROM history
    JOIN items ON items.id = history.item_id
    """)
    history_data = c.fetchall()

    c.execute("SELECT name, qty FROM items WHERE qty <= minimum")
    need_data = c.fetchall()

    c.execute("SELECT name FROM purchase")
    purchase_data = c.fetchall()

    conn.close()

    wb = Workbook()

    ws1 = wb.active
    ws1.title = "Остаток"
    ws1.append(["Название позиции", "Количество"])
    for row in items:
        ws1.append(row)

    ws2 = wb.create_sheet("История")
    ws2.append(["Название позиции", "Количество", "Кто", "Когда"])
    for row in history_data:
        ws2.append(row)

    ws3 = wb.create_sheet("Нужно заказать")
    ws3.append(["Название позиции", "Остаток"])
    for row in need_data:
        ws3.append(row)

    ws4 = wb.create_sheet("Закупка")
    ws4.append(["Название позиции"])
    for row in purchase_data:
        ws4.append(row)

    file_path = "report.xlsx"
    wb.save(file_path)

    with open(file_path, "rb") as f:
        await update.message.reply_document(f)


# ROUTERS

async def msg_router(update, context):

    text = update.message.text

    if text == "📦 В наличии":
        await categories(update, context)

    elif text == "📋 Нужно заказать":
        await need(update, context)

    elif text == "📊 Excel отчет":
        await excel(update, context)


async def cb_router(update, context):

    data = update.callback_query.data

    if data.startswith("cat_"):
        await show_items(update, context)

    elif data == "back_categories":
        await categories(update.callback_query.message, context)

    elif data.startswith("del_item_"):
        await delete_item(update, context)

    elif data.startswith("del_cat_"):
        await delete_category(update, context)

    elif data == "add_category":
        return await add_category_start(update, context)

    elif data == "add_purchase":
        return await add_purchase_start(update, context)


# MAIN

def main():

    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(add_category_start, pattern="add_category")],
            states={
                ADD_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_category_save)]
            },
            fallbacks=[]
        )
    )

    app.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(add_purchase_start, pattern="add_purchase")],
            states={
                ADD_PURCHASE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_purchase_save)]
            },
            fallbacks=[]
        )
    )

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_router))
    app.add_handler(CallbackQueryHandler(cb_router))

    print("BOT STARTED")

    app.run_polling()


if __name__ == "__main__":
    main()
