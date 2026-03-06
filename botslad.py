import logging
import os
import psycopg2
import io
from datetime import datetime
from threading import Thread
from flask import Flask

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

logging.basicConfig(level=logging.INFO)

ASK_NAME = 1
ADD_CATEGORY = 2
ADD_ITEM_NAME = 3
ADD_ITEM_QTY = 4
ADD_ITEM_MIN = 5
CHANGE_QTY = 6
ADD_PURCHASE = 7


# ---------- KEEP ALIVE (RENDER FIX) ----------

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "bot alive"


def run_web():
    flask_app.run(host="0.0.0.0", port=10000)


def keep_alive():
    t = Thread(target=run_web)
    t.start()


# ---------- DATABASE ----------

def db():
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        sslmode="require"
    )


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
        name TEXT UNIQUE
    )
    """)

    # индекс для ускорения истории
    c.execute("""
    CREATE INDEX IF NOT EXISTS idx_history_date
    ON history(date)
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

    await update.message.reply_text(
        "Регистрация завершена",
        reply_markup=main_kb(user.id)
    )

    return ConversationHandler.END


# ---------- AUTO PURCHASE CHECK ----------

def auto_purchase(item_id):

    conn=db()
    c=conn.cursor()

    c.execute("SELECT name,qty,minimum FROM items WHERE id=%s",(item_id,))
    r=c.fetchone()

    if r and r[1] <= r[2]:

        c.execute("""
        INSERT INTO purchase(name)
        VALUES(%s)
        ON CONFLICT(name) DO NOTHING
        """,(r[0],))

    conn.commit()
    conn.close()


# ---------- CHANGE QTY ----------

async def change_save(update,context):

    try:
        qty=int(update.message.text)
    except:
        await update.message.reply_text("Введите число")
        return CHANGE_QTY

    if context.user_data["mode"]=="minus":
        qty=-qty

    conn=db()
    c=conn.cursor()

    c.execute(
        "UPDATE items SET qty=qty+%s WHERE id=%s",
        (qty,context.user_data["item"])
    )

    c.execute("""
    INSERT INTO history(item_id,qty,action,user_name,date)
    VALUES(%s,%s,%s,%s,%s)
    """,(
        context.user_data["item"],
        qty,
        context.user_data["mode"],
        update.effective_user.full_name,
        datetime.now()
    ))

    conn.commit()
    conn.close()

    auto_purchase(context.user_data["item"])

    await update.message.reply_text("Готово")

    return ConversationHandler.END


# ---------- NEED ----------

async def need(update,context):

    conn=db()
    c=conn.cursor()

    c.execute("""
    SELECT name,qty FROM items
    WHERE qty<=minimum
    """)

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

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(kb)
    )


# ---------- EXCEL ----------

async def excel(update,context):

    conn=db()
    c=conn.cursor()

    c.execute("SELECT name,qty,minimum FROM items")
    items=c.fetchall()

    c.execute("""
    SELECT items.name,history.qty,history.user_name,history.date
    FROM history
    JOIN items ON items.id=history.item_id
    WHERE history.date > NOW()-INTERVAL '30 days'
    """)

    hist=c.fetchall()

    c.execute("SELECT name FROM purchase")
    buy=c.fetchall()

    conn.close()

    wb=Workbook()

    ws1=wb.active
    ws1.title="Остаток"

    ws1.append(["Название","Количество"])

    for r in items:
        ws1.append((r[0],r[1]))

    ws2=wb.create_sheet("История")
    ws2.append(["Название","Количество","Кто","Когда"])

    for r in hist:
        ws2.append(r)

    ws3=wb.create_sheet("Нужно заказать")
    ws3.append(["Название"])

    for r in items:
        if r[1] <= r[2]:
            ws3.append([r[0]])

    for r in buy:
        ws3.append(r)

    file = io.BytesIO()
    wb.save(file)
    file.seek(0)

    await update.message.reply_document(file, filename="report.xlsx")


# ---------- ERROR HANDLER ----------

async def error_handler(update, context):
    logging.error(context.error)


# ---------- MAIN ----------

def main():

    keep_alive()
    init_db()

    app=ApplicationBuilder().token(TOKEN).build()

    app.bot.delete_webhook(drop_pending_updates=True)

    app.add_handler(CommandHandler("start",start))

    app.add_error_handler(error_handler)

    print("BOT STARTED")

    app.run_polling(drop_pending_updates=True)


if __name__=="__main__":
    main()
