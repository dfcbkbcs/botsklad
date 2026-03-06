import logging
import os
import psycopg2
from datetime import datetime, timedelta
import asyncio

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

# ---------- CATEGORIES ----------

async def categories(update,context):

    conn=db()
    c=conn.cursor()

    c.execute("""
    SELECT id,name FROM categories
    ORDER BY sort_order
    """)

    rows=c.fetchall()
    conn.close()

    kb=[]

    for r in rows:
        kb.append([InlineKeyboardButton(r[1],callback_data=f"cat_{r[0]}")])

    kb.append([InlineKeyboardButton("➕ Добавить категорию",callback_data="add_cat")])
    kb.append([InlineKeyboardButton("🗑 Удалить категорию",callback_data="del_cat")])
    kb.append([InlineKeyboardButton("⬅ Назад",callback_data="back_main")])

    await update.message.reply_text(
        "Категории:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ---------- SHOW ITEMS ----------

async def show_items(update,context):

    query=update.callback_query
    await query.answer()

    cat=int(query.data.split("_")[1])

    context.user_data["cat"]=cat

    conn=db()
    c=conn.cursor()

    c.execute("""
    SELECT id,name,qty,minimum
    FROM items
    WHERE category_id=%s
    ORDER BY name
    """,(cat,))

    rows=c.fetchall()
    conn.close()

    kb=[]

    for r in rows:

        status="⚠" if r[2]<=r[3] else "✅"

        kb.append([
            InlineKeyboardButton(
                f"{r[1]} ({r[2]}) {status}",
                callback_data=f"item_{r[0]}"
            )
        ])

    kb.append([InlineKeyboardButton("➕ Добавить позицию",callback_data="add_item")])
    kb.append([InlineKeyboardButton("🗑 Удалить позицию",callback_data="del_item")])
    kb.append([InlineKeyboardButton("⬅ Назад",callback_data="back_cat")])

    await query.message.reply_text(
        "Позиции:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ---------- ADD ITEM ----------

async def add_item_start(update,context):

    await update.callback_query.answer()

    await update.callback_query.message.reply_text("Название позиции:")

    return ADD_ITEM_NAME


async def add_item_name(update,context):

    context.user_data["name"]=update.message.text

    await update.message.reply_text("Количество:")

    return ADD_ITEM_QTY


async def add_item_qty(update,context):

    context.user_data["qty"]=int(update.message.text)

    await update.message.reply_text("Минимальный остаток:")

    return ADD_ITEM_MIN


async def add_item_min(update,context):

    conn=db()
    c=conn.cursor()

    c.execute("""
    INSERT INTO items(name,category_id,qty,minimum)
    VALUES(%s,%s,%s,%s)
    """,(
        context.user_data["name"],
        context.user_data["cat"],
        context.user_data["qty"],
        int(update.message.text)
    ))

    conn.commit()
    conn.close()

    await update.message.reply_text("Позиция добавлена")

    return ConversationHandler.END

# ---------- ITEM MENU ----------

async def item_menu(update,context):

    query=update.callback_query
    await query.answer()

    item=int(query.data.split("_")[1])

    context.user_data["item"]=item

    kb=[

        [
            InlineKeyboardButton("➖ Взять",callback_data="minus"),
            InlineKeyboardButton("➕ Добавить",callback_data="plus")
        ],

        [InlineKeyboardButton("📜 История",callback_data="item_history")],

        [InlineKeyboardButton("⬅ Назад",callback_data="back_cat")]

    ]

    await query.message.reply_text(
        "Выберите действие:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ---------- CHANGE QTY ----------

async def plus(update,context):

    await update.callback_query.answer()

    context.user_data["mode"]="plus"

    await update.callback_query.message.reply_text("Количество:")

    return CHANGE_QTY


async def minus(update,context):

    await update.callback_query.answer()

    context.user_data["mode"]="minus"

    await update.callback_query.message.reply_text("Количество:")

    return CHANGE_QTY


async def change_save(update,context):

    qty=int(update.message.text)

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

# ---------- PURCHASE ----------

async def add_purchase_start(update,context):

    await update.callback_query.answer()

    await update.callback_query.message.reply_text("Название позиции:")

    return ADD_PURCHASE


async def add_purchase_save(update,context):

    conn=db()
    c=conn.cursor()

    c.execute("INSERT INTO purchase(name) VALUES(%s)",(update.message.text,))

    conn.commit()
    conn.close()

    await update.message.reply_text("Добавлено")

    return ConversationHandler.END

# ---------- EXCEL ----------

async def excel(update, context):

    conn = db()
    c = conn.cursor()

    c.execute("SELECT name,qty FROM items")
    items = c.fetchall()

    c.execute("""
    SELECT items.name,history.qty,history.user_name,history.date
    FROM history
    JOIN items ON items.id=history.item_id
    WHERE history.date::timestamp > NOW()-INTERVAL '30 days'
    """)
    hist = c.fetchall()

    c.execute("SELECT name FROM purchase")
    buy = c.fetchall()

    c.execute("""
    SELECT name,qty,minimum FROM items
    WHERE qty<=minimum
    """)
    low = c.fetchall()

    conn.close()

    wb = Workbook()

    ws1 = wb.active
    ws1.title = "Остаток"
    ws1.append(["Название", "Количество"])
    for r in items:
        ws1.append(r)

    ws2 = wb.create_sheet("История")
    ws2.append(["Название", "Количество", "Кто", "Когда"])
    for r in hist:
        ws2.append(r)

    ws3 = wb.create_sheet("Нужно заказать")
    ws3.append(["Название", "Тип"])
    for r in low:
        ws3.append([r[0], "Минимальный остаток"])
    for r in buy:
        ws3.append([r[0], "Ручная закупка"])

    file = "report.xlsx"
    wb.save(file)

    with open(file, "rb") as f:
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

async def cb_router(update, context):
    query = update.callback_query
    await query.answer()
    d = query.data

    if d.startswith("cat_"):
        await show_items(update, context)

    elif d.startswith("item_"):
        await item_menu(update, context)

    elif d == "add_item":
        return await add_item_start(update, context)

    elif d == "plus":
        return await plus(update, context)

    elif d == "minus":
        return await minus(update, context)

    elif d == "add_purchase":
        return await add_purchase_start(update, context)

    elif d == "back_main":
        await query.message.reply_text(
            "Главное меню",
            reply_markup=main_kb(update.effective_user.id)
        )

    elif d == "back_cat":
        fake_update = Update(
            update.update_id,
            message=query.message
        )
        await categories(fake_update, context)

# ---------- CALLBACK ----------

async def cb_router(update,context):

    d=update.callback_query.data

    if d.startswith("cat_"):
        await show_items(update,context)

    elif d.startswith("item_"):
        await item_menu(update,context)

    elif d=="add_item":
        return await add_item_start(update,context)

    elif d=="plus":
        return await plus(update,context)

    elif d=="minus":
        return await minus(update,context)

    elif d=="add_purchase":
        return await add_purchase_start(update,context)

# ---------- KEEP ALIVE ----------
import asyncio
import logging

async def keep_alive():
    while True:
        logging.info("KEEP ALIVE PING")
        await asyncio.sleep(600)  # раз в 10 минут

# ---------- MAIN ----------
def main():
    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    # --- handlers ---
    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("start", start)],
            states={ASK_NAME: [MessageHandler(filters.TEXT, save_name)]},
            fallbacks=[]
        )
    )

    app.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(add_item_start, pattern="add_item")],
            states={
                ADD_ITEM_NAME: [MessageHandler(filters.TEXT, add_item_name)],
                ADD_ITEM_QTY: [MessageHandler(filters.TEXT, add_item_qty)],
                ADD_ITEM_MIN: [MessageHandler(filters.TEXT, add_item_min)],
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
                CHANGE_QTY: [MessageHandler(filters.TEXT, change_save)]
            },
            fallbacks=[]
        )
    )

    app.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(add_purchase_start, pattern="add_purchase")],
            states={
                ADD_PURCHASE: [MessageHandler(filters.TEXT, add_purchase_save)]
            },
            fallbacks=[]
        )
    )

    app.add_handler(MessageHandler(filters.TEXT, msg_router))
    app.add_handler(CallbackQueryHandler(cb_router))

    print("BOT STARTED")

    # --- keep alive и запуск через async context ---
    async def runner():
        # запускаем keep_alive параллельно
        asyncio.create_task(keep_alive())
        # запускаем бота
        await app.run_polling()

    # запускаем event loop через asyncio.run
    import asyncio
    asyncio.run(runner())

if __name__ == "__main__":
    main()
