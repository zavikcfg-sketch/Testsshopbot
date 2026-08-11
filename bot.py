import asyncio
import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

YOOMONEY_WALLET = os.getenv(
    "YOOMONEY_WALLET",
    "",
).strip()

YOOMONEY_SECRET = os.getenv(
    "YOOMONEY_SECRET",
    "",
).strip()

WEBHOOK_BASE_URL = os.getenv(
    "WEBHOOK_BASE_URL",
    "",
).strip().rstrip("/")

TELEGRAM_WEBHOOK_SECRET = os.getenv(
    "TELEGRAM_WEBHOOK_SECRET",
    "",
).strip()

ADMIN_IDS_RAW = os.getenv(
    "ADMIN_IDS",
    "8346538289",
).strip()

ADMIN_IDS = {
    int(x.strip())
    for x in ADMIN_IDS_RAW.split(",")
    if x.strip().isdigit()
}

PORT = int(
    os.getenv(
        "PORT",
        "10000",
    )
)

SHOP_NAME = "Digital Market"
CURRENCY = "₽"

DB_FILE = "store.db"
FILES_DIR = Path("files")
FILES_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

TELEGRAM_WEBHOOK_PATH = "/telegram/webhook"
YOOMONEY_WEBHOOK_PATH = "/yoomoney"

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("digital-market")

# ============================================================
# BOT
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
    ),
)

dp = Dispatcher()

# ============================================================
# ADMIN TEMPORARY STATES
# ============================================================

admin_states = {}

# ============================================================
# HELPERS
# ============================================================


def now_iso():
    return datetime.now(
        timezone.utc
    ).isoformat()


def money(value):
    return f"{float(value):,.2f}".replace(
        ",",
        " ",
    )


def validate_config():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан."
        )

    if not YOOMONEY_WALLET:
        raise RuntimeError(
            "YOOMONEY_WALLET не задан."
        )

    if YOOMONEY_WALLET.startswith(
        "41001X"
    ):
        raise RuntimeError(
            "Укажи настоящий YOOMONEY_WALLET."
        )

    if not YOOMONEY_SECRET:
        raise RuntimeError(
            "YOOMONEY_SECRET не задан."
        )

    if not WEBHOOK_BASE_URL:
        raise RuntimeError(
            "WEBHOOK_BASE_URL не задан."
        )

    if not ADMIN_IDS:
        raise RuntimeError(
            "ADMIN_IDS не задан."
        )


def is_admin(user_id):
    return user_id in ADMIN_IDS


# ============================================================
# DATABASE
# ============================================================


def db():
    connection = sqlite3.connect(
        DB_FILE,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_db():
    connection = db()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            price REAL NOT NULL,
            file_path TEXT NOT NULL,
            original_filename TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            product_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            label TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            operation_id TEXT,
            created_at TEXT NOT NULL,
            paid_at TEXT
        )
        """
    )

    connection.commit()
    connection.close()


# ============================================================
# USERS
# ============================================================


def save_user(
    user_id,
    username,
    first_name,
):
    connection = db()

    connection.execute(
        """
        INSERT INTO users
        (
            user_id,
            username,
            first_name,
            created_at
        )
        VALUES (?, ?, ?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name
        """,
        (
            user_id,
            username,
            first_name,
            now_iso(),
        ),
    )

    connection.commit()
    connection.close()


# ============================================================
# PRODUCTS
# ============================================================


def get_products():
    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM products
        ORDER BY id DESC
        """
    ).fetchall()

    connection.close()

    return rows


def get_product(product_id):
    connection = db()

    row = connection.execute(
        """
        SELECT *
        FROM products
        WHERE id=?
        """,
        (product_id,),
    ).fetchone()

    connection.close()

    return row


def add_product(
    name,
    description,
    price,
    file_path,
    original_filename,
):
    connection = db()

    cursor = connection.execute(
        """
        INSERT INTO products
        (
            name,
            description,
            price,
            file_path,
            original_filename,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            description,
            price,
            file_path,
            original_filename,
            now_iso(),
        ),
    )

    product_id = cursor.lastrowid

    connection.commit()
    connection.close()

    return product_id


def delete_product(product_id):
    connection = db()

    connection.execute(
        """
        DELETE FROM products
        WHERE id=?
        """,
        (product_id,),
    )

    connection.commit()
    connection.close()


# ============================================================
# ORDERS
# ============================================================


def create_order(
    user_id,
    username,
    product_id,
    amount,
):
    label = (
        "TG-"
        + secrets.token_hex(10).upper()
    )

    connection = db()

    cursor = connection.execute(
        """
        INSERT INTO orders
        (
            user_id,
            username,
            product_id,
            amount,
            label,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            user_id,
            username,
            product_id,
            amount,
            label,
            now_iso(),
        ),
    )

    order_id = cursor.lastrowid

    connection.commit()
    connection.close()

    return order_id, label


def get_order_by_label(label):
    connection = db()

    row = connection.execute(
        """
        SELECT *
        FROM orders
        WHERE label=?
        """,
        (label,),
    ).fetchone()

    connection.close()

    return row


def get_user_orders(user_id):
    connection = db()

    rows = connection.execute(
        """
        SELECT
            orders.*,
            products.name AS product_name
        FROM orders
        LEFT JOIN products
            ON products.id = orders.product_id
        WHERE orders.user_id=?
        ORDER BY orders.id DESC
        """,
        (user_id,),
    ).fetchall()

    connection.close()

    return rows


def mark_order_paid(
    label,
    operation_id,
):
    connection = db()

    row = connection.execute(
        """
        SELECT *
        FROM orders
        WHERE label=?
          AND status='pending'
        """,
        (label,),
    ).fetchone()

    if not row:
        connection.close()
        return None

    connection.execute(
        """
        UPDATE orders
        SET
            status='paid',
            operation_id=?,
            paid_at=?
        WHERE label=?
        """,
        (
            operation_id,
            now_iso(),
            label,
        ),
    )

    connection.commit()
    connection.close()

    return row


# ============================================================
# STATISTICS
# ============================================================


def statistics():
    connection = db()

    users = connection.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    products = connection.execute(
        "SELECT COUNT(*) FROM products"
    ).fetchone()[0]

    orders = connection.execute(
        "SELECT COUNT(*) FROM orders"
    ).fetchone()[0]

    paid_orders = connection.execute(
        """
        SELECT COUNT(*)
        FROM orders
        WHERE status='paid'
        """
    ).fetchone()[0]

    revenue = connection.execute(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM orders
        WHERE status='paid'
        """
    ).fetchone()[0]

    connection.close()

    return (
        users,
        products,
        orders,
        paid_orders,
        revenue,
    )


# ============================================================
# BUTTON HELPERS
#
# Telegram Bot API supports:
# primary = blue
# success = green
# danger  = red
# ============================================================


def button(
    text,
    callback_data=None,
    url=None,
    style="primary",
):
    return InlineKeyboardButton(
        text=text,
        callback_data=callback_data,
        url=url,
        style=style,
    )


# ============================================================
# MAIN MENU
# ============================================================


def main_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(
                    "🛍 Каталог",
                    "catalog",
                    style="primary",
                ),
                button(
                    "📦 Мои покупки",
                    "purchases",
                    style="success",
                ),
            ],
            [
                button(
                    "💎 Как это работает",
                    "how",
                    style="primary",
                ),
                button(
                    "💬 Поддержка",
                    "support",
                    style="primary",
                ),
            ],
        ]
    )


def home_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(
                    "🛍 Открыть каталог",
                    "catalog",
                    style="primary",
                ),
            ],
            [
                button(
                    "📦 Мои покупки",
                    "purchases",
                    style="success",
                ),
            ],
        ]
    )


# ============================================================
# CATALOG KEYBOARD
# ============================================================


def catalog_keyboard(products):
    rows = []

    for product in products:
        rows.append(
            [
                button(
                    (
                        f"📄 {product['name']} "
                        f"• {money(product['price'])} {CURRENCY}"
                    ),
                    f"product:{product['id']}",
                    style="primary",
                )
            ]
        )

    rows.append(
        [
            button(
                "🏠 Главное меню",
                "home",
                style="primary",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# PRODUCT KEYBOARD
# ============================================================


def product_keyboard(product):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(
                    (
                        f"💳 Купить "
                        f"• {money(product['price'])} {CURRENCY}"
                    ),
                    f"buy:{product['id']}",
                    style="success",
                )
            ],
            [
                button(
                    "◀️ В каталог",
                    "catalog",
                    style="primary",
                )
            ],
        ]
    )


# ============================================================
# PAYMENT KEYBOARD
# ============================================================


def payment_keyboard(
    payment_url,
    label,
):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(
                    "💳 ОПЛАТИТЬ ЮMONEY",
                    url=payment_url,
                    style="success",
                )
            ],
            [
                button(
                    "🔄 Проверить оплату",
                    f"check:{label}",
                    style="primary",
                )
            ],
            [
                button(
                    "❌ Отменить заказ",
                    "catalog",
                    style="danger",
                )
            ],
        ]
    )


# ============================================================
# ADMIN MENU
# ============================================================


def admin_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(
                    "➕ Добавить товар",
                    "admin:add",
                    style="success",
                )
            ],
            [
                button(
                    "📦 Управление товарами",
                    "admin:products",
                    style="primary",
                )
            ],
            [
                button(
                    "📊 Статистика",
                    "admin:stats",
                    style="primary",
                )
            ],
            [
                button(
                    "🏠 Магазин",
                    "home",
                    style="primary",
                )
            ],
        ]
    )


def admin_products_keyboard():
    rows = []

    products = get_products()

    for product in products:
        rows.append(
            [
                button(
                    (
                        f"🗑 {product['name']} "
                        f"• {money(product['price'])} ₽"
                    ),
                    f"admin:delete:{product['id']}",
                    style="danger",
                )
            ]
        )

    rows.append(
        [
            button(
                "➕ Добавить товар",
                "admin:add",
                style="success",
            )
        ]
    )

    rows.append(
        [
            button(
                "◀️ Админ-панель",
                "admin",
                style="primary",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# TEXTS
# ============================================================


def welcome_text(first_name=None):
    name = first_name or "друг"

    return (
        f"✨ <b>{SHOP_NAME}</b>\n\n"
        f"Привет, <b>{name}</b>! 👋\n\n"
        "Добро пожаловать в магазин цифровых товаров.\n\n"
        "╭────────────────────╮\n"
        "│ 📁 <b>Цифровые файлы</b>\n"
        "│ ⚡ <b>Мгновенная выдача</b>\n"
        "│ 💳 <b>Оплата через ЮMoney</b>\n"
        "│ 🔐 <b>Уникальный заказ</b>\n"
        "╰────────────────────╯\n\n"
        "Выберите нужный раздел ниже 👇"
    )


# ============================================================
# START
# ============================================================


@dp.message(CommandStart())
async def start(message: Message):
    if not message.from_user:
        return

    save_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

    await message.answer(
        welcome_text(
            message.from_user.first_name
        ),
        reply_markup=main_menu(),
    )


# ============================================================
# HOME
# ============================================================


@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery):
    await callback.answer()

    await callback.message.edit_text(
        welcome_text(
            callback.from_user.first_name
        ),
        reply_markup=main_menu(),
    )


# ============================================================
# CATALOG
# ============================================================


@dp.callback_query(F.data == "catalog")
async def catalog(callback: CallbackQuery):
    await callback.answer()

    products = get_products()

    if not products:
        await callback.message.edit_text(
            "🛍 <b>Каталог</b>\n\n"
            "Пока здесь нет товаров.\n\n"
            "Загляните позже — новые цифровые "
            "товары скоро появятся.",
            reply_markup=main_menu(),
        )
        return

    text = (
        "🛍 <b>КАТАЛОГ</b>\n\n"
        "Выберите цифровой товар:\n\n"
        "⚡ После оплаты файл будет "
        "отправлен автоматически."
    )

    await callback.message.edit_text(
        text,
        reply_markup=catalog_keyboard(
            products
        ),
    )


# ============================================================
# PRODUCT
# ============================================================


@dp.callback_query(
    F.data.startswith("product:")
)
async def product_view(
    callback: CallbackQuery,
):
    await callback.answer()

    try:
        product_id = int(
            callback.data.split(":")[1]
        )
    except Exception:
        return

    product = get_product(product_id)

    if not product:
        await callback.answer(
            "Товар не найден.",
            show_alert=True,
        )
        return

    text = (
        "╭────────────────────╮\n"
        "│ 📦 <b>ТОВАР</b>\n"
        "╰────────────────────╯\n\n"
        f"📄 <b>{product['name']}</b>\n\n"
        f"{product['description']}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💰 Цена: <b>{money(product['price'])} {CURRENCY}</b>\n"
        "⚡ Выдача: <b>мгновенно</b>\n"
        "💳 Оплата: <b>ЮMoney</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "После подтверждения оплаты "
        "файл автоматически придёт сюда."
    )

    await callback.message.edit_text(
        text,
        reply_markup=product_keyboard(
            product
        ),
    )


# ============================================================
# BUY
# ============================================================


@dp.callback_query(
    F.data.startswith("buy:")
)
async def buy(
    callback: CallbackQuery,
):
    try:
        product_id = int(
            callback.data.split(":")[1]
        )
    except Exception:
        await callback.answer(
            "Ошибка товара.",
            show_alert=True,
        )
        return

    product = get_product(product_id)

    if not product:
        await callback.answer(
            "Товар не найден.",
            show_alert=True,
        )
        return

    order_id, label = create_order(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        product_id=product_id,
        amount=float(product["price"]),
    )

    payment_url = (
        "https://yoomoney.ru/quickpay/confirm?"
        + urllib.parse.urlencode(
            {
                "receiver": YOOMONEY_WALLET,
                "quickpay-form": "button",
                "sum": f"{product['price']:.2f}",
                "paymentType": "AC",
                "label": label,
            }
        )
    )

    text = (
        "💳 <b>ОФОРМЛЕНИЕ ЗАКАЗА</b>\n\n"
        f"📦 Товар:\n"
        f"<b>{product['name']}</b>\n\n"
        f"💰 К оплате: "
        f"<b>{money(product['price'])} {CURRENCY}</b>\n"
        f"🧾 Заказ: <code>#{order_id}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "1️⃣ Нажмите «Оплатить ЮMoney».\n"
        "2️⃣ Завершите оплату.\n"
        "3️⃣ Нажмите «Проверить оплату», "
        "если файл ещё не пришёл.\n\n"
        "⚡ После подтверждения платежа "
        "товар будет отправлен автоматически."
    )

    await callback.message.edit_text(
        text,
        reply_markup=payment_keyboard(
            payment_url,
            label,
        ),
    )

    await callback.answer()


# ============================================================
# CHECK PAYMENT
# ============================================================


@dp.callback_query(
    F.data.startswith("check:")
)
async def check_payment(
    callback: CallbackQuery,
):
    label = callback.data.split(
        ":",
        1,
    )[1]

    order = get_order_by_label(label)

    if not order:
        await callback.answer(
            "Заказ не найден.",
            show_alert=True,
        )
        return

    if order["user_id"] != callback.from_user.id:
        await callback.answer(
            "Этот заказ принадлежит другому пользователю.",
            show_alert=True,
        )
        return

    if order["status"] == "paid":
        await callback.answer(
            "✅ Оплата уже подтверждена.",
            show_alert=True,
        )
        return

    await callback.answer(
        "⏳ Платёж ещё не подтверждён.\n\n"
        "Если вы только что оплатили, "
        "подождите несколько секунд и попробуйте снова.",
        show_alert=True,
    )


# ============================================================
# PURCHASES
# ============================================================


@dp.callback_query(
    F.data == "purchases"
)
async def purchases(
    callback: CallbackQuery,
):
    rows = get_user_orders(
        callback.from_user.id
    )

    if not rows:
        await callback.message.edit_text(
            "📦 <b>МОИ ПОКУПКИ</b>\n\n"
            "У вас пока нет заказов.\n\n"
            "Откройте каталог и выберите "
            "первый цифровой товар.",
            reply_markup=home_keyboard(),
        )

        await callback.answer()
        return

    text = (
        "📦 <b>МОИ ПОКУПКИ</b>\n\n"
    )

    for row in rows[:15]:
        if row["status"] == "paid":
            status = "🟢 <b>Оплачен</b>"
        else:
            status = "🟡 <b>Ожидает оплаты</b>"

        text += (
            f"📄 <b>{row['product_name'] or 'Товар'}</b>\n"
            f"💰 {money(row['amount'])} {CURRENCY}\n"
            f"{status}\n"
            f"🧾 <code>{row['label']}</code>\n\n"
        )

    await callback.message.edit_text(
        text,
        reply_markup=main_menu(),
    )

    await callback.answer()


# ============================================================
# HOW IT WORKS
# ============================================================


@dp.callback_query(
    F.data == "how"
)
async def how(
    callback: CallbackQuery,
):
    text = (
        "💎 <b>КАК ЭТО РАБОТАЕТ</b>\n\n"
        "🛍 <b>1. Выберите товар</b>\n"
        "Откройте каталог и выберите нужный файл.\n\n"
        "💳 <b>2. Оплатите заказ</b>\n"
        "Бот создаст уникальный номер заказа "
        "и откроет страницу ЮMoney.\n\n"
        "🔐 <b>3. Платёж подтверждается</b>\n"
        "ЮMoney отправляет уведомление нашему серверу.\n\n"
        "📁 <b>4. Получите файл</b>\n"
        "После подтверждения оплаты бот автоматически "
        "отправит купленный файл в этот чат.\n\n"
        "⚡ <b>Всё автоматически.</b>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(
                    "🛍 Открыть каталог",
                    "catalog",
                    style="primary",
                )
            ],
            [
                button(
                    "🏠 Главное меню",
                    "home",
                    style="primary",
                )
            ],
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
    )

    await callback.answer()


# ============================================================
# SUPPORT
# ============================================================


@dp.callback_query(
    F.data == "support"
)
async def support(
    callback: CallbackQuery,
):
    text = (
        "💬 <b>ПОДДЕРЖКА</b>\n\n"
        "Возникли проблемы с оплатой "
        "или получением файла?\n\n"
        "Пожалуйста, сохраните номер заказа "
        "и обратитесь к администратору магазина.\n\n"
        "🧾 Номер заказа можно найти "
        "в разделе «Мои покупки»."
    )

    await callback.message.edit_text(
        text,
        reply_markup=main_menu(),
    )

    await callback.answer()


# ============================================================
# ADMIN
# ============================================================


@dp.message(Command("admin"))
async def admin_command(
    message: Message,
):
    if not message.from_user:
        return

    if not is_admin(
        message.from_user.id
    ):
        await message.answer(
            "⛔ <b>Доступ запрещён.</b>"
        )
        return

    await message.answer(
        "⚙️ <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "Управление магазином:",
        reply_markup=admin_menu(),
    )


@dp.callback_query(
    F.data == "admin"
)
async def admin_callback(
    callback: CallbackQuery,
):
    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        "⚙️ <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "Выберите действие:",
        reply_markup=admin_menu(),
    )

    await callback.answer()


# ============================================================
# ADMIN STATISTICS
# ============================================================


@dp.callback_query(
    F.data == "admin:stats"
)
async def admin_stats(
    callback: CallbackQuery,
):
    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True,
        )
        return

    (
        users,
        products,
        orders,
        paid_orders,
        revenue,
    ) = statistics()

    text = (
        "📊 <b>СТАТИСТИКА</b>\n\n"
        f"👥 Пользователей: <b>{users}</b>\n"
        f"📦 Товаров: <b>{products}</b>\n"
        f"🧾 Заказов: <b>{orders}</b>\n"
        f"✅ Оплачено: <b>{paid_orders}</b>\n"
        f"💰 Выручка: "
        f"<b>{money(revenue)} {CURRENCY}</b>\n"
    )

    await callback.message.edit_text(
        text,
        reply_markup=admin_menu(),
    )

    await callback.answer()


# ============================================================
# ADMIN PRODUCTS
# ============================================================


@dp.callback_query(
    F.data == "admin:products"
)
async def admin_products(
    callback: CallbackQuery,
):
    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True,
        )
        return

    products = get_products()

    if not products:
        text = (
            "📦 <b>ТОВАРЫ</b>\n\n"
            "Товаров пока нет."
        )
    else:
        text = (
            "📦 <b>ТОВАРЫ</b>\n\n"
        )

        for product in products:
            text += (
                f"#{product['id']} "
                f"<b>{product['name']}</b>\n"
                f"💰 {money(product['price'])} {CURRENCY}\n\n"
            )

    await callback.message.edit_text(
        text,
        reply_markup=admin_products_keyboard(),
    )

    await callback.answer()


# ============================================================
# ADMIN ADD
# ============================================================


@dp.callback_query(
    F.data == "admin:add"
)
async def admin_add(
    callback: CallbackQuery,
):
    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True,
        )
        return

    admin_states[
        callback.from_user.id
    ] = {
        "step": "name"
    }

    await callback.message.edit_text(
        "➕ <b>ДОБАВЛЕНИЕ ТОВАРА</b>\n\n"
        "Шаг <b>1/4</b>\n\n"
        "Введите название товара."
    )

    await callback.answer()


# ============================================================
# ADMIN TEXT INPUT
# ============================================================


@dp.message()
async def admin_input(
    message: Message,
):
    if not message.from_user:
        return

    user_id = message.from_user.id

    if not is_admin(user_id):
        return

    state = admin_states.get(
        user_id
    )

    if not state:
        return

    step = state["step"]

    # NAME
    if step == "name":
        if not message.text:
            await message.answer(
                "❌ Отправьте название текстом."
            )
            return

        state["name"] = (
            message.text.strip()
        )

        state["step"] = "description"

        await message.answer(
            "📝 <b>Шаг 2/4</b>\n\n"
            "Введите описание товара."
        )

        return

    # DESCRIPTION
    if step == "description":
        if not message.text:
            await message.answer(
                "❌ Отправьте описание текстом."
            )
            return

        state["description"] = (
            message.text.strip()
        )

        state["step"] = "price"

        await message.answer(
            "💰 <b>Шаг 3/4</b>\n\n"
            "Введите цену в рублях.\n\n"
            "Например:\n"
            "<code>499</code>"
        )

        return

    # PRICE
    if step == "price":
        if not message.text:
            await message.answer(
                "❌ Введите цену."
            )
            return

        try:
            price = float(
                message.text.replace(
                    ",",
                    ".",
                )
            )

            if price <= 0:
                raise ValueError

        except ValueError:
            await message.answer(
                "❌ Неверная цена.\n\n"
                "Введите положительное число, "
                "например <code>499</code>."
            )
            return

        state["price"] = price
        state["step"] = "file"

        await message.answer(
            "📁 <b>Шаг 4/4</b>\n\n"
            "Отправьте файл как <b>документ</b> Telegram.\n\n"
            "Именно этот файл будет автоматически "
            "выдан покупателю после оплаты."
        )

        return

    # FILE
    if step == "file":
        if not message.document:
            await message.answer(
                "❌ Нужно отправить файл "
                "именно как документ Telegram."
            )
            return

        document = message.document

        original_name = (
            document.file_name
            or "digital-file.bin"
        )

        safe_name = (
            secrets.token_hex(8)
            + "_"
            + Path(
                original_name
            ).name
        )

        destination = (
            FILES_DIR / safe_name
        )

        try:
            telegram_file = (
                await bot.get_file(
                    document.file_id
                )
            )

            await bot.download_file(
                telegram_file.file_path,
                destination=destination,
            )

        except Exception:
            logger.exception(
                "File download failed"
            )

            await message.answer(
                "❌ Не удалось сохранить файл."
            )
            return

        product_id = add_product(
            name=state["name"],
            description=state[
                "description"
            ],
            price=state["price"],
            file_path=str(
                destination
            ),
            original_filename=(
                original_name
            ),
        )

        del admin_states[user_id]

        await message.answer(
            "✅ <b>ТОВАР ДОБАВЛЕН</b>\n\n"
            f"🆔 ID: <code>{product_id}</code>\n"
            f"📄 {state['name']}\n"
            f"💰 {money(state['price'])} {CURRENCY}\n"
            f"📁 {original_name}",
            reply_markup=admin_menu(),
        )


# ============================================================
# ADMIN DELETE
# ============================================================


@dp.callback_query(
    F.data.startswith(
        "admin:delete:"
    )
)
async def admin_delete(
    callback: CallbackQuery,
):
    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True,
        )
        return

    try:
        product_id = int(
            callback.data.split(":")[2]
        )
    except Exception:
        await callback.answer(
            "Ошибка.",
            show_alert=True,
        )
        return

    product = get_product(
        product_id
    )

    if not product:
        await callback.answer(
            "Товар уже удалён.",
            show_alert=True,
        )
        return

    delete_product(
        product_id
    )

    try:
        path = Path(
            product["file_path"]
        )

        if path.exists():
            path.unlink()

    except Exception:
        logger.exception(
            "Could not delete product file"
        )

    await callback.message.edit_text(
        "🗑 <b>ТОВАР УДАЛЁН</b>\n\n"
        f"📄 {product['name']}",
        reply_markup=admin_products_keyboard(),
    )

    await callback.answer()


# ============================================================
# YOOMONEY SIGNATURE
# ============================================================


def verify_yoomoney_signature(
    data,
):
    received_sign = data.get(
        "sign"
    )

    if not received_sign:
        return False

    params = {}

    for key, value in data.items():
        if key == "sign":
            continue

        params[key] = str(value)

    prepared = "&".join(
        (
            urllib.parse.quote(
                str(key),
                safe="-_.~",
            )
            + "="
            + urllib.parse.quote(
                str(value),
                safe="-_.~",
            )
        )
        for key, value
        in sorted(
            params.items()
        )
    )

    expected = hmac.new(
        YOOMONEY_SECRET.encode(
            "utf-8"
        ),
        prepared.encode(
            "utf-8"
        ),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(
        expected.lower(),
        str(
            received_sign
        ).lower(),
    )


# ============================================================
# SEND PRODUCT
# ============================================================


async def send_product_to_user(
    user_id,
    product,
):
    file_path = Path(
        product["file_path"]
    )

    if not file_path.exists():
        logger.error(
            "Product file missing: %s",
            file_path,
        )

        await bot.send_message(
            user_id,
            "⚠️ <b>Оплата подтверждена.</b>\n\n"
            "Но файл товара временно недоступен.\n\n"
            "Администратор уже уведомлён.",
        )

        return False

    await bot.send_message(
        user_id,
        "🎉 <b>ОПЛАТА ПОЛУЧЕНА!</b>\n\n"
        f"📦 Ваш товар: "
        f"<b>{product['name']}</b>\n\n"
        "📁 Сейчас отправлю файл.\n\n"
        "Спасибо за покупку! ❤️",
    )

    await bot.send_document(
        user_id,
        FSInputFile(
            path=file_path,
            filename=(
                product[
                    "original_filename"
                ]
                or file_path.name
            ),
        ),
        caption=(
            f"📄 <b>{product['name']}</b>\n\n"
            "Спасибо за покупку! ❤️"
        ),
    )

    return True


# ============================================================
# YOOMONEY WEBHOOK
# ============================================================


async def yoomoney_webhook(
    request: web.Request,
):
    try:
        post = await request.post()

        data = {
            key: value
            for key, value
            in post.items()
        }

        logger.info(
            "ЮMoney notification: %s",
            {
                k: (
                    "***"
                    if k in {
                        "sign",
                    }
                    else v
                )
                for k, v
                in data.items()
            },
        )

        if not verify_yoomoney_signature(
            data
        ):
            logger.warning(
                "Invalid ЮMoney signature"
            )

            return web.Response(
                status=403,
                text="invalid signature",
            )

        notification_type = (
            data.get(
                "notification_type"
            )
        )

        if notification_type not in {
            "p2p-incoming",
            "card-incoming",
        }:
            return web.Response(
                status=200,
                text="ignored",
            )

        label = data.get(
            "label"
        )

        if not label:
            return web.Response(
                status=200,
                text="no label",
            )

        order = get_order_by_label(
            label
        )

        if not order:
            logger.warning(
                "Unknown order label: %s",
                label,
            )

            return web.Response(
                status=200,
                text="unknown order",
            )

        if order["status"] == "paid":
            return web.Response(
                status=200,
                text="already paid",
            )

        try:
            amount_received = float(
                data.get(
                    "amount",
                    "0",
                )
            )
        except (
            ValueError,
            TypeError,
        ):
            amount_received = 0

        expected_amount = float(
            order["amount"]
        )

        if (
            amount_received
            + 0.0001
            < expected_amount
        ):
            logger.warning(
                "Amount mismatch: "
                "received=%s expected=%s",
                amount_received,
                expected_amount,
            )

            await bot.send_message(
                order["user_id"],
                "⚠️ <b>Платёж получен, "
                "но сумма отличается.</b>\n\n"
                f"Получено: "
                f"<b>{money(amount_received)} {CURRENCY}</b>\n"
                f"Нужно: "
                f"<b>{money(expected_amount)} {CURRENCY}</b>\n\n"
                "Обратитесь в поддержку.",
            )

            return web.Response(
                status=200,
                text="amount mismatch",
            )

        operation_id = data.get(
            "operation_id",
            "",
        )

        paid_order = mark_order_paid(
            label,
            operation_id,
        )

        if not paid_order:
            return web.Response(
                status=200,
                text="already processed",
            )

        product = get_product(
            paid_order[
                "product_id"
            ]
        )

        if not product:
            logger.error(
                "Product not found: %s",
                paid_order[
                    "product_id"
                ],
            )

            await bot.send_message(
                paid_order["user_id"],
                "⚠️ <b>Платёж подтверждён.</b>\n\n"
                "Но товар не найден.\n"
                "Администратор уже уведомлён.",
            )

            return web.Response(
                status=200,
                text="product missing",
            )

        try:
            await send_product_to_user(
                paid_order[
                    "user_id"
                ],
                product,
            )

        except Exception:
            logger.exception(
                "Product delivery failed"
            )

            await bot.send_message(
                paid_order["user_id"],
                "⚠️ <b>Платёж подтверждён.</b>\n\n"
                "Произошла ошибка при отправке файла.\n"
                "Администратор уже уведомлён.",
            )

        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    "💰 <b>НОВАЯ ОПЛАТА</b>\n\n"
                    f"🧾 Заказ: "
                    f"<code>#{paid_order['id']}</code>\n"
                    f"📦 Товар: "
                    f"<b>{product['name']}</b>\n"
                    f"💰 Сумма: "
                    f"<b>{money(amount_received)} {CURRENCY}</b>\n"
                    f"👤 User ID: "
                    f"<code>{paid_order['user_id']}</code>\n"
                    f"🔑 Label: "
                    f"<code>{label}</code>\n"
                    f"🆔 Operation: "
                    f"<code>{operation_id or '—'}</code>",
                )

            except Exception:
                logger.exception(
                    "Admin notification failed"
                )

        return web.Response(
            status=200,
            text="ok",
        )

    except Exception:
        logger.exception(
            "ЮMoney webhook error"
        )

        return web.Response(
            status=200,
            text="error",
        )


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================


async def telegram_webhook(
    request: web.Request,
):
    if TELEGRAM_WEBHOOK_SECRET:
        received_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            "",
        )

        if not hmac.compare_digest(
            received_secret,
            TELEGRAM_WEBHOOK_SECRET,
        ):
            logger.warning(
                "Invalid Telegram webhook secret"
            )

            return web.Response(
                status=403,
                text="forbidden",
            )

    try:
        data = await request.json()

        update = Update.model_validate(
            data
        )

        await dp.feed_update(
            bot,
            update,
        )

        return web.Response(
            status=200,
            text="ok",
        )

    except Exception:
        logger.exception(
            "Telegram webhook error"
        )

        return web.Response(
            status=200,
            text="error",
        )


# ============================================================
# HEALTH
# ============================================================


async def health(
    request: web.Request,
):
    return web.json_response(
        {
            "status": "ok",
            "shop": SHOP_NAME,
            "telegram": "webhook",
            "payment": "yoomoney",
        }
    )


# ============================================================
# ROOT
# ============================================================


async def root(
    request: web.Request,
):
    return web.Response(
        text=(
            f"{SHOP_NAME} is running."
        )
    )


# ============================================================
# WEB SERVER
# ============================================================


async def start_web_server():
    app = web.Application()

    app.router.add_get(
        "/",
        root,
    )

    app.router.add_get(
        "/health",
        health,
    )

    app.router.add_post(
        TELEGRAM_WEBHOOK_PATH,
        telegram_webhook,
    )

    app.router.add_post(
        YOOMONEY_WEBHOOK_PATH,
        yoomoney_webhook,
    )

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=PORT,
    )

    await site.start()

    logger.info(
        "HTTP server started on port %s",
        PORT,
    )

    return runner


# ============================================================
# SET TELEGRAM WEBHOOK
# ============================================================


async def configure_telegram():
    webhook_url = (
        WEBHOOK_BASE_URL
        + TELEGRAM_WEBHOOK_PATH
    )

    await bot.set_webhook(
        url=webhook_url,
        secret_token=(
            TELEGRAM_WEBHOOK_SECRET
            or None
        ),
        allowed_updates=[
            "message",
            "callback_query",
        ],
        drop_pending_updates=False,
    )

    logger.info(
        "Telegram webhook: %s",
        webhook_url,
    )


# ============================================================
# BOT COMMANDS
# ============================================================


async def configure_commands():
    await bot.set_my_commands(
        [
            {
                "command": "start",
                "description": "Открыть магазин",
            },
            {
                "command": "catalog",
                "description": "Открыть каталог",
            },
            {
                "command": "purchases",
                "description": "Мои покупки",
            },
            {
                "command": "support",
                "description": "Поддержка",
            },
        ]
    )


@dp.message(
    Command("catalog")
)
async def catalog_command(
    message: Message,
):
    products = get_products()

    if not products:
        await message.answer(
            "🛍 <b>Каталог</b>\n\n"
            "Пока товаров нет.",
            reply_markup=main_menu(),
        )
        return

    await message.answer(
        "🛍 <b>КАТАЛОГ</b>\n\n"
        "Выберите товар:",
        reply_markup=catalog_keyboard(
            products
        ),
    )


@dp.message(
    Command("purchases")
)
async def purchases_command(
    message: Message,
):
    if not message.from_user:
        return

    rows = get_user_orders(
        message.from_user.id
    )

    if not rows:
        await message.answer(
            "📦 <b>МОИ ПОКУПКИ</b>\n\n"
            "Покупок пока нет.",
            reply_markup=main_menu(),
        )
        return

    text = (
        "📦 <b>МОИ ПОКУПКИ</b>\n\n"
    )

    for row in rows[:15]:
        status = (
            "🟢 Оплачен"
            if row["status"] == "paid"
            else "🟡 Ожидает оплаты"
        )

        text += (
            f"📄 <b>{row['product_name'] or 'Товар'}</b>\n"
            f"💰 {money(row['amount'])} {CURRENCY}\n"
            f"{status}\n"
            f"🧾 <code>{row['label']}</code>\n\n"
        )

    await message.answer(
        text,
        reply_markup=main_menu(),
    )


@dp.message(
    Command("support")
)
async def support_command(
    message: Message,
):
    await message.answer(
        "💬 <b>ПОДДЕРЖКА</b>\n\n"
        "Если возникла проблема с заказом, "
        "сохраните номер заказа и обратитесь "
        "к администратору.",
        reply_markup=main_menu(),
    )


# ============================================================
# STARTUP
# ============================================================


async def main():
    validate_config()
    init_db()

    logger.info(
        "Starting %s",
        SHOP_NAME,
    )

    web_runner = (
        await start_web_server()
    )

    try:
        await configure_commands()
        await configure_telegram()

        logger.info(
            "================================="
        )

        logger.info(
            "%s started successfully",
            SHOP_NAME,
        )

        logger.info(
            "Telegram webhook: %s%s",
            WEBHOOK_BASE_URL,
            TELEGRAM_WEBHOOK_PATH,
        )

        logger.info(
            "ЮMoney webhook: %s%s",
            WEBHOOK_BASE_URL,
            YOOMONEY_WEBHOOK_PATH,
        )

        logger.info(
            "================================="
        )

        # Для webhook режима polling НЕ запускаем.
        # Render держит процесс через aiohttp.
        await asyncio.Event().wait()

    finally:
        await bot.delete_webhook(
            drop_pending_updates=False
        )

        await web_runner.cleanup()

        await bot.session.close()


# ============================================================
# RUN
# ============================================================


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info(
            "Bot stopped."
        )
