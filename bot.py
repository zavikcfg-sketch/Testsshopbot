# ============================================================
# DIGITAL MARKET — TELEGRAM DIGITAL STORE
# Python 3.12+
# aiogram 3.x + aiohttp + SQLite
# Render Webhook
# ЮMoney
# ============================================================
#
# requirements.txt:
#
# aiogram>=3.22,<4
# aiohttp>=3.12,<4
#
# Render:
#
# Build Command:
# pip install -r requirements.txt
#
# Start Command:
# python bot.py
#
# ОБЯЗАТЕЛЬНЫЕ ENV VARIABLES:
#
# BOT_TOKEN
# YOOMONEY_WALLET
# YOOMONEY_SECRET
# WEBHOOK_BASE_URL
# TELEGRAM_WEBHOOK_SECRET
#
# НЕ ХРАНИ ТОКЕНЫ В КОДЕ.
#
# ============================================================

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

PORT = int(
    os.getenv(
        "PORT",
        "10000",
    )
)

SHOP_NAME = os.getenv(
    "SHOP_NAME",
    "Digital Market",
).strip()

CURRENCY = "₽"

ADMIN_IDS = {
    8346538289,
}

FILES_DIR = Path("files")
FILES_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

DB_FILE = "store.db"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(message)s"
    ),
)

logger = logging.getLogger("digital-market")


# ============================================================
# HELPERS
# ============================================================

def utc_now() -> str:
    """
    Python 3.12+
    Вместо deprecated datetime.utcnow()
    """
    return datetime.now(
        timezone.utc
    ).isoformat()


def money(value: float) -> str:
    return f"{float(value):,.2f}".replace(
        ",",
        " ",
    )


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def env_ok(value: str) -> bool:
    return bool(value)


# ============================================================
# VALIDATION
# ============================================================

def validate_config():
    errors = []

    if not BOT_TOKEN:
        errors.append(
            "BOT_TOKEN не задан."
        )

    if not YOOMONEY_WALLET:
        errors.append(
            "YOOMONEY_WALLET не задан."
        )

    if not YOOMONEY_SECRET:
        errors.append(
            "YOOMONEY_SECRET не задан."
        )

    if not WEBHOOK_BASE_URL:
        errors.append(
            "WEBHOOK_BASE_URL не задан."
        )

    if not TELEGRAM_WEBHOOK_SECRET:
        errors.append(
            "TELEGRAM_WEBHOOK_SECRET не задан."
        )

    if errors:
        raise RuntimeError(
            "\n".join(
                f"• {error}"
                for error in errors
            )
        )


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
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            price REAL NOT NULL,
            file_path TEXT NOT NULL,
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

    connection.commit()
    connection.close()

    logger.info(
        "Database initialized"
    )


# ============================================================
# USERS
# ============================================================

def save_user(
    user_id: int,
    username: str | None,
    first_name: str | None,
):
    connection = db()

    connection.execute(
        """
        INSERT INTO users (
            user_id,
            username,
            first_name,
            created_at
        )
        VALUES (?, ?, ?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name
        """,
        (
            user_id,
            username,
            first_name,
            utc_now(),
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


def get_product(product_id: int):
    connection = db()

    row = connection.execute(
        """
        SELECT *
        FROM products
        WHERE id = ?
        """,
        (product_id,),
    ).fetchone()

    connection.close()

    return row


def add_product(
    name: str,
    description: str,
    price: float,
    file_path: str,
):
    connection = db()

    cursor = connection.execute(
        """
        INSERT INTO products (
            name,
            description,
            price,
            file_path,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            name,
            description,
            price,
            file_path,
            utc_now(),
        ),
    )

    product_id = cursor.lastrowid

    connection.commit()
    connection.close()

    return product_id


def delete_product(product_id: int):
    connection = db()

    connection.execute(
        """
        DELETE FROM products
        WHERE id = ?
        """,
        (product_id,),
    )

    connection.commit()
    connection.close()


# ============================================================
# ORDERS
# ============================================================

def create_order(
    user_id: int,
    username: str | None,
    product_id: int,
    amount: float,
):
    label = (
        "TG-"
        + secrets.token_hex(10).upper()
    )

    connection = db()

    cursor = connection.execute(
        """
        INSERT INTO orders (
            user_id,
            username,
            product_id,
            amount,
            label,
            status,
            created_at
        )
        VALUES (
            ?,
            ?,
            ?,
            ?,
            ?,
            'pending',
            ?
        )
        """,
        (
            user_id,
            username,
            product_id,
            amount,
            label,
            utc_now(),
        ),
    )

    order_id = cursor.lastrowid

    connection.commit()
    connection.close()

    return order_id, label


def get_order_by_label(label: str):
    connection = db()

    row = connection.execute(
        """
        SELECT *
        FROM orders
        WHERE label = ?
        """,
        (label,),
    ).fetchone()

    connection.close()

    return row


def mark_order_paid(
    label: str,
    operation_id: str,
):
    connection = db()

    row = connection.execute(
        """
        SELECT *
        FROM orders
        WHERE label = ?
          AND status = 'pending'
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
            status = 'paid',
            operation_id = ?,
            paid_at = ?
        WHERE label = ?
        """,
        (
            operation_id,
            utc_now(),
            label,
        ),
    )

    connection.commit()
    connection.close()

    return row


def get_user_orders(user_id: int):
    connection = db()

    rows = connection.execute(
        """
        SELECT
            orders.*,
            products.name AS product_name
        FROM orders
        LEFT JOIN products
            ON products.id = orders.product_id
        WHERE orders.user_id = ?
        ORDER BY orders.id DESC
        """,
        (user_id,),
    ).fetchall()

    connection.close()

    return rows


# ============================================================
# STATISTICS
# ============================================================

def statistics():
    connection = db()

    users = connection.execute(
        """
        SELECT COUNT(*)
        FROM users
        """
    ).fetchone()[0]

    products = connection.execute(
        """
        SELECT COUNT(*)
        FROM products
        """
    ).fetchone()[0]

    orders = connection.execute(
        """
        SELECT COUNT(*)
        FROM orders
        """
    ).fetchone()[0]

    paid_orders = connection.execute(
        """
        SELECT COUNT(*)
        FROM orders
        WHERE status = 'paid'
        """
    ).fetchone()[0]

    revenue = connection.execute(
        """
        SELECT COALESCE(
            SUM(amount),
            0
        )
        FROM orders
        WHERE status = 'paid'
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
# MAIN MENU
# ============================================================

def main_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛍  Каталог",
                    callback_data="catalog",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📦  Мои покупки",
                    callback_data="purchases",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💎  Как это работает",
                    callback_data="how",
                ),
                InlineKeyboardButton(
                    text="💬  Поддержка",
                    callback_data="support",
                ),
            ],
        ]
    )


def home_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛍  Открыть каталог",
                    callback_data="catalog",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📦  Мои покупки",
                    callback_data="purchases",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💎  Как это работает",
                    callback_data="how",
                ),
                InlineKeyboardButton(
                    text="💬  Поддержка",
                    callback_data="support",
                ),
            ],
        ]
    )


# ============================================================
# PRODUCT KEYBOARD
# ============================================================

def product_keyboard(
    product_id: int,
):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳  Купить сейчас",
                    callback_data=(
                        f"buy:{product_id}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="◀️  Назад к каталогу",
                    callback_data="catalog",
                ),
            ],
        ]
    )


# ============================================================
# PAYMENT URL
# ============================================================

def create_payment_url(
    wallet: str,
    amount: float,
    label: str,
):
    params = {
        "receiver": wallet,
        "quickpay-form": "button",
        "sum": f"{amount:.2f}",
        "paymentType": "AC",
        "label": label,
    }

    encoded = urllib.parse.urlencode(
        params
    )

    return (
        "https://yoomoney.ru/quickpay/confirm?"
        + encoded
    )


def payment_keyboard(
    payment_url: str,
    label: str,
):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳  ОПЛАТИТЬ ЮMONEY",
                    url=payment_url,
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄  Проверить оплату",
                    callback_data=(
                        f"check:{label}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="◀️  Вернуться в каталог",
                    callback_data="catalog",
                ),
            ],
        ]
    )


# ============================================================
# ADMIN KEYBOARD
# ============================================================

def admin_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕  Добавить товар",
                    callback_data="admin_add",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📦  Управление товарами",
                    callback_data="admin_products",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊  Статистика",
                    callback_data="admin_stats",
                ),
            ],
        ]
    )


def admin_products_keyboard():
    rows = []

    products = get_products()

    for product in products:
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"🗑  {product['name']}"
                    ),
                    callback_data=(
                        f"delete:{product['id']}"
                    ),
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️  Панель администратора",
                callback_data="admin",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# /START
# ============================================================

@dp.message(CommandStart())
async def start(
    message: Message,
):
    if not message.from_user:
        return

    save_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
    )

    first_name = (
        message.from_user.first_name
        or "друг"
    )

    text = (
        f"✨ <b>{SHOP_NAME}</b>\n\n"
        f"Привет, <b>{first_name}</b>! 👋\n\n"
        "Добро пожаловать в магазин "
        "цифровых товаров.\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📁 <b>Цифровые товары</b>\n"
        "⚡ <b>Мгновенная выдача</b>\n"
        "💳 <b>Оплата через ЮMoney</b>\n"
        "🔐 <b>Безопасное оформление</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Выберите нужный раздел ниже:</i>"
    )

    await message.answer(
        text,
        reply_markup=home_keyboard(),
    )


# ============================================================
# CATALOG
# ============================================================

@dp.callback_query(
    F.data == "catalog"
)
async def catalog(
    callback: CallbackQuery,
):
    products = get_products()

    if not products:
        text = (
            "🛍 <b>КАТАЛОГ</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "📦 Сейчас товаров нет.\n\n"
            "Загляните позже."
        )

        await callback.message.edit_text(
            text,
            reply_markup=main_menu(),
        )

        await callback.answer()
        return

    buttons = []

    for product in products:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"📄 {product['name']} "
                        f"• {money(product['price'])} ₽"
                    ),
                    callback_data=(
                        f"product:{product['id']}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🏠  Главное меню",
                callback_data="home",
            )
        ]
    )

    text = (
        "🛍 <b>КАТАЛОГ ТОВАРОВ</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите товар, чтобы посмотреть "
        "подробную информацию.\n\n"
        "⚡ После оплаты файл будет отправлен "
        "автоматически."
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await callback.answer()


# ============================================================
# PRODUCT VIEW
# ============================================================

@dp.callback_query(
    F.data.startswith("product:")
)
async def product_view(
    callback: CallbackQuery,
):
    try:
        product_id = int(
            callback.data.split(
                ":",
                1,
            )[1]
        )
    except (
        ValueError,
        IndexError,
    ):
        await callback.answer(
            "Ошибка товара.",
            show_alert=True,
        )
        return

    product = get_product(
        product_id
    )

    if not product:
        await callback.answer(
            "Товар не найден.",
            show_alert=True,
        )
        return

    text = (
        "📄 <b>ТОВАР</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"✨ <b>{product['name']}</b>\n\n"
        f"{product['description']}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 Цена: "
        f"<b>{money(product['price'])} ₽</b>\n"
        "⚡ Получение: <b>мгновенно</b>\n"
        "📁 Формат: <b>цифровой файл</b>\n\n"
        "<i>После успешной оплаты бот "
        "автоматически отправит файл.</i>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=product_keyboard(
            product_id
        ),
    )

    await callback.answer()


# ============================================================
# BUY
# ============================================================

@dp.callback_query(
    F.data.startswith("buy:")
)
async def buy(
    callback: CallbackQuery,
):
    if not callback.from_user:
        return

    try:
        product_id = int(
            callback.data.split(
                ":",
                1,
            )[1]
        )
    except (
        ValueError,
        IndexError,
    ):
        await callback.answer(
            "Ошибка товара.",
            show_alert=True,
        )
        return

    product = get_product(
        product_id
    )

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
        amount=float(
            product["price"]
        ),
    )

    payment_url = create_payment_url(
        wallet=YOOMONEY_WALLET,
        amount=float(
            product["price"]
        ),
        label=label,
    )

    keyboard = payment_keyboard(
        payment_url=payment_url,
        label=label,
    )

    text = (
        "💳 <b>ОФОРМЛЕНИЕ ЗАКАЗА</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 Товар:\n"
        f"<b>{product['name']}</b>\n\n"
        f"💰 К оплате:\n"
        f"<b>{money(product['price'])} ₽</b>\n\n"
        f"🧾 Заказ: "
        f"<code>#{order_id}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Как оплатить:</b>\n\n"
        "1️⃣ Нажмите «ОПЛАТИТЬ ЮMONEY».\n"
        "2️⃣ Завершите оплату.\n"
        "3️⃣ Вернитесь в бот.\n"
        "4️⃣ Нажмите «Проверить оплату».\n\n"
        "⚡ После подтверждения платежа "
        "файл будет отправлен автоматически."
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
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

    order = get_order_by_label(
        label
    )

    if not order:
        await callback.answer(
            "Заказ не найден.",
            show_alert=True,
        )
        return

    if order["user_id"] != callback.from_user.id:
        await callback.answer(
            "Этот заказ вам не принадлежит.",
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
        "подождите несколько секунд "
        "и попробуйте снова.",
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
        text = (
            "📦 <b>МОИ ПОКУПКИ</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "У вас пока нет заказов.\n\n"
            "🛍 Перейдите в каталог "
            "и выберите товар."
        )

        await callback.message.edit_text(
            text,
            reply_markup=main_menu(),
        )

        await callback.answer()
        return

    text = (
        "📦 <b>МОИ ПОКУПКИ</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    for row in rows[:15]:
        if row["status"] == "paid":
            status = "✅ Оплачен"
        else:
            status = "⏳ Ожидает оплаты"

        text += (
            f"📄 <b>"
            f"{row['product_name'] or 'Товар'}"
            f"</b>\n"
            f"💰 {money(row['amount'])} ₽\n"
            f"{status}\n"
            f"🧾 <code>{row['label']}</code>\n\n"
        )

    await callback.message.edit_text(
        text,
        reply_markup=main_menu(),
    )

    await callback.answer()


# ============================================================
# HOW
# ============================================================

@dp.callback_query(
    F.data == "how"
)
async def how(
    callback: CallbackQuery,
):
    text = (
        "💎 <b>КАК ЭТО РАБОТАЕТ</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🛍 <b>1. Выберите товар</b>\n"
        "Откройте каталог и выберите "
        "нужный цифровой товар.\n\n"
        "💳 <b>2. Оплатите</b>\n"
        "Бот создаст уникальный заказ "
        "и отправит вас на страницу ЮMoney.\n\n"
        "🔐 <b>3. Подтверждение</b>\n"
        "После поступления платежа "
        "бот проверит заказ.\n\n"
        "📁 <b>4. Получите файл</b>\n"
        "Файл будет автоматически "
        "отправлен в этот чат.\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ Быстро\n"
        "🔐 Безопасно\n"
        "📁 Автоматически"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛍  Каталог",
                    callback_data="catalog",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏠  Главное меню",
                    callback_data="home",
                ),
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
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Возникла проблема с заказом, "
        "оплатой или получением файла?\n\n"
        "Напишите администратору "
        "магазина.\n\n"
        "🕐 Мы постараемся помочь "
        "как можно быстрее."
    )

    await callback.message.edit_text(
        text,
        reply_markup=main_menu(),
    )

    await callback.answer()


# ============================================================
# HOME
# ============================================================

@dp.callback_query(
    F.data == "home"
)
async def home(
    callback: CallbackQuery,
):
    text = (
        f"✨ <b>{SHOP_NAME}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Цифровые товары "
        "с мгновенной выдачей.\n\n"
        "📁 <b>Файлы</b>\n"
        "💳 <b>ЮMoney</b>\n"
        "⚡ <b>Автоматическая доставка</b>\n"
        "🔐 <b>Уникальные заказы</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Выберите раздел:</i>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=home_keyboard(),
    )

    await callback.answer()


# ============================================================
# ADMIN
# ============================================================

@dp.message(
    Command("admin")
)
async def admin(
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
        "⚙️ <b>ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
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
        "⚙️ <b>ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите действие:",
        reply_markup=admin_menu(),
    )

    await callback.answer()


# ============================================================
# ADMIN STATISTICS
# ============================================================

@dp.callback_query(
    F.data == "admin_stats"
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
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Пользователей: "
        f"<b>{users}</b>\n\n"
        f"📦 Товаров: "
        f"<b>{products}</b>\n\n"
        f"🧾 Заказов: "
        f"<b>{orders}</b>\n\n"
        f"✅ Оплачено: "
        f"<b>{paid_orders}</b>\n\n"
        f"💰 Выручка: "
        f"<b>{money(revenue)} ₽</b>\n\n"
        "━━━━━━━━━━━━━━━━━━"
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
    F.data == "admin_products"
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
            "━━━━━━━━━━━━━━━━━━\n\n"
        )

        for product in products:
            text += (
                f"#{product['id']} "
                f"<b>{product['name']}</b>\n"
                f"💰 {money(product['price'])} ₽\n\n"
            )

    await callback.message.edit_text(
        text,
        reply_markup=admin_products_keyboard(),
    )

    await callback.answer()


# ============================================================
# ADMIN ADD PRODUCT STATE
# ============================================================

admin_states = {}


@dp.callback_query(
    F.data == "admin_add"
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
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Шаг 1 из 4\n\n"
        "📄 Введите название товара:"
    )

    await callback.answer()


# ============================================================
# ADMIN INPUT
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

    # --------------------------------------------------------
    # NAME
    # --------------------------------------------------------

    if step == "name":
        if not message.text:
            await message.answer(
                "❌ Отправьте название текстом."
            )
            return

        name = message.text.strip()

        if not name:
            await message.answer(
                "❌ Название не может быть пустым."
            )
            return

        state["name"] = name
        state["step"] = "description"

        await message.answer(
            "📝 <b>Шаг 2 из 4</b>\n\n"
            "Введите описание товара:"
        )

        return

    # --------------------------------------------------------
    # DESCRIPTION
    # --------------------------------------------------------

    if step == "description":
        if not message.text:
            await message.answer(
                "❌ Отправьте описание текстом."
            )
            return

        description = message.text.strip()

        if not description:
            await message.answer(
                "❌ Описание не может быть пустым."
            )
            return

        state["description"] = description
        state["step"] = "price"

        await message.answer(
            "💰 <b>Шаг 3 из 4</b>\n\n"
            "Введите цену в рублях.\n\n"
            "Например:\n"
            "<code>499</code>"
        )

        return

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

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
                ).strip()
            )
        except ValueError:
            await message.answer(
                "❌ Неверная цена.\n\n"
                "Пример: <code>499</code>"
            )
            return

        if price <= 0:
            await message.answer(
                "❌ Цена должна быть больше нуля."
            )
            return

        state["price"] = price
        state["step"] = "file"

        await message.answer(
            "📁 <b>Шаг 4 из 4</b>\n\n"
            "Отправьте файл товара "
            "<b>как документ</b> Telegram."
        )

        return

    # --------------------------------------------------------
    # FILE
    # --------------------------------------------------------

    if step == "file":
        if not message.document:
            await message.answer(
                "❌ Нужно отправить файл "
                "именно как документ."
            )
            return

        document = message.document

        original_name = (
            document.file_name
            or "file.bin"
        )

        safe_name = (
            secrets.token_hex(8)
            + "_"
            + Path(
                original_name
            ).name
        )

        destination = (
            FILES_DIR
            / safe_name
        )

        try:
            tg_file = await bot.get_file(
                document.file_id
            )

            await bot.download_file(
                tg_file.file_path,
                destination=destination,
            )

        except Exception as error:
            logger.exception(
                "File download error: %s",
                error,
            )

            await message.answer(
                "❌ Не удалось сохранить файл."
            )

            return

        product_id = add_product(
            name=state["name"],
            description=state["description"],
            price=state["price"],
            file_path=str(
                destination
            ),
        )

        product_name = state["name"]
        product_price = state["price"]

        del admin_states[user_id]

        await message.answer(
            "✅ <b>ТОВАР ДОБАВЛЕН!</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 ID: <code>{product_id}</code>\n"
            f"📄 <b>{product_name}</b>\n"
            f"💰 {money(product_price)} ₽\n"
            f"📁 {original_name}\n\n"
            "Товар уже появился в каталоге."
        )

        return


# ============================================================
# ADMIN DELETE
# ============================================================

@dp.callback_query(
    F.data.startswith("delete:")
)
async def delete_product_callback(
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
            callback.data.split(
                ":",
                1,
            )[1]
        )
    except (
        ValueError,
        IndexError,
    ):
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
        f"Товар <b>{product['name']}</b> "
        "удалён из магазина.",
        reply_markup=admin_products_keyboard(),
    )

    await callback.answer()


# ============================================================
# YOOMONEY SIGNATURE
# ============================================================

def verify_yoomoney_signature(
    data: dict,
) -> bool:

    received_sign = data.get(
        "sign"
    )

    if not received_sign:
        return False

    params = {}

    for key, value in data.items():
        if key == "sign":
            continue

        if isinstance(
            value,
            list,
        ):
            value = value[0]

        params[str(key)] = str(
            value
        )

    sorted_items = sorted(
        params.items(),
        key=lambda item: item[0],
    )

    parts = []

    for key, value in sorted_items:
        encoded_key = urllib.parse.quote(
            str(key),
            safe="-_.~",
        )

        encoded_value = urllib.parse.quote(
            str(value),
            safe="-_.~",
        )

        parts.append(
            encoded_key
            + "="
            + encoded_value
        )

    prepared = "&".join(
        parts
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
    user_id: int,
    product,
):
    file_path = Path(
        product["file_path"]
    )

    if not file_path.exists():

        await bot.send_message(
            user_id,
            "⚠️ <b>Оплата подтверждена.</b>\n\n"
            "Но файл товара сейчас "
            "недоступен.\n\n"
            "Администратор уже уведомлён.",
        )

        logger.error(
            "Product file missing: %s",
            file_path,
        )

        return

    await bot.send_message(
        user_id,
        "🎉 <b>ОПЛАТА ПОЛУЧЕНА!</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 <b>{product['name']}</b>\n\n"
        "📁 Ваш файл отправляется ниже.\n\n"
        "Спасибо за покупку! ❤️",
    )

    await bot.send_document(
        user_id,
        FSInputFile(
            path=file_path,
            filename=file_path.name,
        ),
        caption=(
            f"📄 <b>{product['name']}</b>\n\n"
            "Спасибо за покупку! ❤️"
        ),
    )


# ============================================================
# YOOMONEY WEBHOOK
# ============================================================

async def yoomoney_webhook(
    request: web.Request,
):
    try:
        post = await request.post()

        data = {
            str(key): str(value)
            for key, value in post.items()
        }

        logger.info(
            "ЮMoney notification received: %s",
            data,
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

        notification_type = data.get(
            "notification_type"
        )

        if notification_type not in (
            "p2p-incoming",
            "card-incoming",
        ):
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
        except ValueError:
            amount_received = 0.0

        expected_amount = float(
            order["amount"]
        )

        if (
            amount_received + 0.0001
            < expected_amount
        ):
            logger.warning(
                "Amount mismatch: received=%s expected=%s",
                amount_received,
                expected_amount,
            )

            await bot.send_message(
                order["user_id"],
                "⚠️ <b>Платёж получен.</b>\n\n"
                "Но сумма отличается "
                "от суммы заказа.\n\n"
                f"Получено: "
                f"<b>{money(amount_received)} ₽</b>\n"
                f"Нужно: "
                f"<b>{money(expected_amount)} ₽</b>\n\n"
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
            label=label,
            operation_id=operation_id,
        )

        if not paid_order:
            return web.Response(
                status=200,
                text="already processed",
            )

        product = get_product(
            paid_order["product_id"]
        )

        if not product:

            logger.error(
                "Product not found: %s",
                paid_order["product_id"],
            )

            await bot.send_message(
                paid_order["user_id"],
                "⚠️ <b>Оплата подтверждена.</b>\n\n"
                "Но товар не найден.\n\n"
                "Администратор уже уведомлён.",
            )

            return web.Response(
                status=200,
                text="product missing",
            )

        try:
            await send_product_to_user(
                paid_order["user_id"],
                product,
            )

        except Exception as error:

            logger.exception(
                "Could not send product: %s",
                error,
            )

            await bot.send_message(
                paid_order["user_id"],
                "⚠️ <b>Оплата подтверждена.</b>\n\n"
                "Возникла ошибка при отправке "
                "файла.\n\n"
                "Администратор уже уведомлён.",
            )

        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    "💰 <b>НОВАЯ ОПЛАТА</b>\n\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    f"🧾 Заказ: "
                    f"<code>#{paid_order['id']}</code>\n"
                    f"📦 Товар: "
                    f"<b>{product['name']}</b>\n"
                    f"💰 Сумма: "
                    f"<b>{money(amount_received)} ₽</b>\n"
                    f"👤 User ID: "
                    f"<code>{paid_order['user_id']}</code>\n"
                    f"🔑 Label: "
                    f"<code>{label}</code>\n"
                    f"🆔 Operation: "
                    f"<code>{operation_id}</code>",
                )
            except Exception:
                logger.exception(
                    "Could not notify admin"
                )

        return web.Response(
            status=200,
            text="ok",
        )

    except Exception as error:

        logger.exception(
            "ЮMoney webhook error: %s",
            error,
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
    incoming_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token",
        "",
    )

    if not hmac.compare_digest(
        incoming_secret,
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
        update_data = await request.json()

        from aiogram.types import Update

        update = Update.model_validate(
            update_data
        )

        await dp.feed_update(
            bot,
            update,
        )

        return web.Response(
            status=200,
            text="ok",
        )

    except Exception as error:

        logger.exception(
            "Telegram webhook error: %s",
            error,
        )

        return web.Response(
            status=500,
            text="error",
        )


# ============================================================
# HEALTH
# ============================================================

async def health(
    request: web.Request,
):
    return web.Response(
        status=200,
        text=(
            "Digital Market Bot is running."
        ),
    )


async def root(
    request: web.Request,
):
    return web.Response(
        status=200,
        text=(
            "Digital Market Bot is online."
        ),
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
        "/telegram/webhook",
        telegram_webhook,
    )

    app.router.add_post(
        "/yoomoney",
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
# TELEGRAM WEBHOOK SETUP
# ============================================================

async def setup_telegram_webhook():
    webhook_url = (
        WEBHOOK_BASE_URL
        + "/telegram/webhook"
    )

    await bot.set_webhook(
        url=webhook_url,
        secret_token=(
            TELEGRAM_WEBHOOK_SECRET
        ),
        drop_pending_updates=True,
        allowed_updates=[
            "message",
            "callback_query",
        ],
    )

    logger.info(
        "Telegram webhook URL: %s",
        webhook_url,
    )


# ============================================================
# STARTUP
# ============================================================

async def main():

    validate_config()

    init_db()

    web_runner = await start_web_server()

    try:

        await setup_telegram_webhook()

        logger.info(
            "================================="
        )

        logger.info(
            "%s started successfully",
            SHOP_NAME,
        )

        logger.info(
            "Telegram webhook: %s/telegram/webhook",
            WEBHOOK_BASE_URL,
        )

        logger.info(
            "ЮMoney webhook: %s/yoomoney",
            WEBHOOK_BASE_URL,
        )

        logger.info(
            "================================="
        )

        await asyncio.Event().wait()

    finally:

        try:
            await bot.delete_webhook()
        except Exception:
            pass

        await web_runner.cleanup()

        await bot.session.close()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    try:
        asyncio.run(
            main()
        )

    except KeyboardInterrupt:
        logger.info(
            "Bot stopped."
        )
