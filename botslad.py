import logging
import os
import psycopg2
import asyncio
import threading

from datetime import datetime
from openpyxl import Workbook
from flask import Flask

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

logging.basicConfig(level=logging.INFO)

ASK_NAME = 1
ADD_CATEGORY = 2
ADD_ITEM_NAME = 3
ADD_ITEM_QTY = 4
ADD_ITEM_MIN = 5
CHANGE_QTY = 6
ADD_PURCHASE = 7


# ---------- WEB SERVER (Render) ----------

web_app = Flask(__name__)

@web_app.route("/")
def home():
    return "Bot is running"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)


# ---------- DATABASE ----------

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
        sort_order INTEGER
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
        date TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS purchase(
        id SERIAL PRIMARY KEY,
        name TEXT
    )
    """)

    defaults = [
        ("Расходники",1),
        ("Материал",2),
        ("Инструмент",3),
        ("Металл",4)
    ]

    for name, order in defaults:
        c.execute("""
        INSERT INTO categories(name,sort_order)
        VALUES(%s,%s)
        ON CONFLICT(name) DO NOTHING
        """,(name,order))

    conn.commit()
    conn.close()


# ---------- ROLE ----------

def is_admin(uid):

    if uid == OWNER_ID:
        return True

    conn=db()
    c=conn.cursor()

    c.execute("SELECT role FROM users WHERE tg_id=%s",(uid,))
    r=c.fetchone()

    conn.close()

    return r and r[0]=="admin"


# ---------- KEYBOARD ----------

def main_kb(uid):

    kb=[
        ["📦 В наличии","📋 Нужно заказать"],
        ["👥 Пользователи","📜 Общая история"],
        ["📊 Excel отчет"]
    ]

    return ReplyKeyboardMarkup(kb,resize_keyboard=True)


# ---------- START ----------

async def start(update:Update,context):

    user=update.effective_user

    conn=db()
    c=conn.cursor()

    c.execute("SELECT name FROM users WHERE tg_id=%s",(user.id,))
    r=c.fetchone()

    conn.close()

    if not r:
        await update.message.reply_text("Введите ваше имя:")
        return ASK_NAME

    await update.message.reply_text(
        "Главное меню",
        reply_markup=main_kb(user.id)
    )


async def save_name(update:Update,context):

    name=update.message.text
    user=update.effective_user

    conn=db()
    c=conn.cursor()

    role="admin" if user.id==OWNER_ID else "user"

    c.execute("""
    INSERT INTO users(tg_id,name,role)
    VALUES(%s,%s,%s)
    ON CONFLICT(tg_id) DO NOTHING
    """,(user.id,name,role))

    conn.commit()
    conn.close()

    await update.message.reply_text("Регистрация завершена")

    await start(update,context)

    return ConversationHandler.END


# ---------- CATEGORIES ----------

async def categories(update,context):

    context.user_data["previous_state"] = "main"

    conn=db()
    c=conn.cursor()

    c.execute("SELECT id,name FROM categories ORDER BY sort_order")
    rows=c.fetchall()

    conn.close()

    kb=[]

    for r in rows:
        kb.append([InlineKeyboardButton(r[1],callback_data=f"cat_{r[0]}")])

    kb.append([InlineKeyboardButton("➕ Добавить категорию",callback_data="add_cat")])
    kb.append([InlineKeyboardButton("🗑 Удалить категорию",callback_data="del_cat")])
    kb.append([InlineKeyboardButton("⬅ Назад",callback_data="back_main")])

    if update.message:
        await update.message.reply_text("Категории:", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.callback_query.message.reply_text("Категории:", reply_markup=InlineKeyboardMarkup(kb))


# ---------- ITEMS ----------

async def show_items(update,context):

    context.user_data["previous_state"] = "categories"

    query=update.callback_query
    await query.answer()

    cat=int(query.data.split("_")[1])
    context.user_data["cat"]=cat

    await show_items_for_category(query, context, cat)


async def show_items_for_category(query, context, cat_id):

    context.user_data["cat"] = cat_id
    context.user_data["previous_state"] = "categories"

    conn = db()
    c = conn.cursor()

    c.execute("SELECT id,name,qty,minimum FROM items WHERE category_id=%s ORDER BY name",(cat_id,))
    rows = c.fetchall()

    conn.close()

    kb=[
        [InlineKeyboardButton(f"{r[1]} ({r[2]}) {'⚠' if r[2]<=r[3] else '✅'}", callback_data=f"item_{r[0]}")]
        for r in rows
    ]

    kb.append([InlineKeyboardButton("➕ Добавить позицию", callback_data="add_item")])
    kb.append([InlineKeyboardButton("🗑 Удалить позицию", callback_data="del_item")])
    kb.append([InlineKeyboardButton("⬅ Назад", callback_data="back_cat")])

    await query.message.reply_text("Позиции:", reply_markup=InlineKeyboardMarkup(kb))


# ---------- BACK ----------

async def go_back(update, context):

    query = update.callback_query
    await query.answer()

    prev = context.user_data.get("previous_state","main")

    if prev=="main":

        await query.message.reply_text(
            "Главное меню",
            reply_markup=main_kb(update.effective_user.id)
        )

    elif prev=="categories":

        await categories(update,context)

    elif prev=="items":

        cat_id = context.user_data.get("cat")

        if cat_id:
            await show_items_for_category(query, context, cat_id)

        else:
            await categories(update,context)


# ---------- NEED ----------

async def need(update,context):

    context.user_data["previous_state"] = "main"

    conn=db()
    c=conn.cursor()

    c.execute("SELECT name,qty,minimum FROM items WHERE qty<=minimum")
    low=c.fetchall()

    c.execute("SELECT id,name FROM purchase")
    buy=c.fetchall()

    conn.close()

    text="⚠ Нужно пополнить\n\n"

    text+="\n".join(f"{r[0]} ({r[1]})" for r in low) or "Нет позиций"

    text+="\n\n🛒 Список закупки\n"

    text+="\n".join(r[1] for r in buy) or "Пусто"

    kb=[
        [InlineKeyboardButton("➕ Добавить",callback_data="add_purchase")],
        [InlineKeyboardButton("⬅ Назад",callback_data="back_main")]
    ]

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))


# ---------- EXCEL ----------

async def excel(update,context):

    conn=db()
    c=conn.cursor()

    c.execute("SELECT name,qty FROM items")
    items=c.fetchall()

    conn.close()

    wb=Workbook()

    ws=wb.active
    ws.append(["Название","Количество"])

    for r in items:
        ws.append(r)

    file="report.xlsx"
    wb.save(file)

    with open(file,"rb") as f:
        await update.message.reply_document(f)


# ---------- ROUTERS ----------

async def msg_router(update,context):

    t=update.message.text

    if t=="📦 В наличии":
        await categories(update,context)

    elif t=="📋 Нужно заказать":
        await need(update,context)

    elif t=="📊 Excel отчет":
        await excel(update,context)


async def cb_router(update,context):

    d = update.callback_query.data

    if d.startswith("cat_"):
        await show_items(update,context)

    elif d.startswith("back"):
        await go_back(update,context)


# ---------- KEEP ALIVE ----------

async def keep_alive():

    while True:

        logging.info("KEEP ALIVE PING")

        await asyncio.sleep(600)


# ---------- MAIN ----------

def main():

    init_db()

    threading.Thread(target=run_web, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("start", start)],
            states={
                ASK_NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND, save_name)]
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
