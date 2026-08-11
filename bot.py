# ============================================================
# SEVAN MARKET
# Telegram Digital Store + ЮMoney + Render
#
# Python 3.12+
#
# requirements.txt:
# aiogram==3.22.0
# aiohttp==3.12.15
#
# Start Command:
# python bot.py
#
# ============================================================

import asyncio
import hashlib
import hmac
import logging
import math
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

# ВАЖНО:
# После публикации токена в переписке перевыпусти его через
# @BotFather и вставь новый токен в переменную окружения.
#
# Render:
# Environment -> Environment Variables
#
BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    "PASTE_NEW_BOT_TOKEN_HERE",
)

# Настоящий номер кошелька ЮMoney.
YOOMONEY_WALLET = os.getenv(
    "YOOMONEY_WALLET",
    "PASTE_YOOMONEY_WALLET_HERE",
)

# Секрет из:
# ЮMoney -> Настройки -> HTTP-уведомления -> Показать секрет
YOOMONEY_SECRET = os.getenv(
    "YOOMONEY_SECRET",
    "PASTE_YOOMONEY_SECRET_HERE",
)

# Render URL.
#
# У тебя сейчас:
# https://testsshopbot-1.onrender.com
#
WEBHOOK_BASE_URL = os.getenv(
    "WEBHOOK_BASE_URL",
    "https://testsshopbot-1.onrender.com",
).rstrip("/")

TELEGRAM_WEBHOOK_PATH = "/telegram/webhook"
YOOMONEY_WEBHOOK_PATH = "/yoomoney"

TELEGRAM_WEBHOOK_URL = (
    WEBHOOK_BASE_URL + TELEGRAM_WEBHOOK_PATH
)

YOOMONEY_WEBHOOK_URL = (
    WEBHOOK_BASE_URL + YOOMONEY_WEBHOOK_PATH
)

# Render автоматически передаёт PORT.
WEB_PORT = int(
    os.getenv("PORT", "10000")
)

# ============================================================
# ADMIN
# ============================================================

ADMIN_IDS = {
    8346538289,
}

# ============================================================
# SHOP
# ============================================================

SHOP_NAME = "SEVAN MARKET"

CURRENCY = "₽"

# ------------------------------------------------------------
# КОМИССИЯ ЮMONEY
# ------------------------------------------------------------
#
# Если комиссия 3%, ставим:
#
# 0.03
#
# Товар 10 ₽:
#
# 10 / (1 - 0.03) = 10.309...
#
# Округляем вверх:
#
# 10.31 ₽
#
# После комиссии примерно 10.00 ₽.
#
# ------------------------------------------------------------

YOOMONEY_FEE_RATE = 0.03

# Минимальная разница при проверке копеек.
MONEY_EPSILON = 0.001

# ============================================================
# PATHS
# ============================================================

FILES_DIR = Path("files")
FILES_DIR.mkdir(parents=True, exist_ok=True)

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

logger = logging.getLogger("sevan_market")


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


# ============================================================
# MONEY
# ============================================================

def money_ceil(value: float) -> float:
    """
    Округление суммы вверх до копейки.
    """
    return math.ceil(
        (value - 1e-9) * 100
    ) / 100


def calculate_customer_amount(
    product_price: float,
) -> float:
    """
    Рассчитывает сумму, которую должен
    заплатить покупатель, чтобы после
    комиссии на кошелёк пришла стоимость товара.
    """

    if YOOMONEY_FEE_RATE <= 0:
        return round(product_price, 2)

    result = (
        product_price
        / (1 - YOOMONEY_FEE_RATE)
    )

    return money_ceil(result)


def calculate_fee(
    customer_amount: float,
    net_amount: float,
) -> float:

    return round(
        max(
            customer_amount - net_amount,
            0
        ),
        2,
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
            customer_amount REAL NOT NULL,
            commission REAL NOT NULL,

            label TEXT UNIQUE NOT NULL,

            status TEXT NOT NULL
                DEFAULT 'pending',

            operation_id TEXT,

            received_amount REAL,
            withdraw_amount REAL,

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
        WHERE id=?
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
    original_filename: str,
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
    user_id: int,
    username: str | None,
    product_id: int,
    product_price: float,
):

    customer_amount = calculate_customer_amount(
        product_price
    )

    commission = calculate_fee(
        customer_amount,
        product_price,
    )

    label = (
        "SEVAN-"
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
            customer_amount,
            commission,
            label,
            status,
            created_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, 'pending', ?
        )
        """,
        (
            user_id,
            username,
            product_id,
            product_price,
            customer_amount,
            commission,
            label,
            utc_now(),
        ),
    )

    order_id = cursor.lastrowid

    connection.commit()
    connection.close()

    return (
        order_id,
        label,
        customer_amount,
        commission,
    )


def get_order_by_label(label: str):

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


def get_order_by_operation(
    operation_id: str,
):

    connection = db()

    row = connection.execute(
        """
        SELECT *
        FROM orders
        WHERE operation_id=?
        """,
        (operation_id,),
    ).fetchone()

    connection.close()

    return row


def mark_order_paid(
    label: str,
    operation_id: str,
    received_amount: float,
    withdraw_amount: float,
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
            received_amount=?,
            withdraw_amount=?,
            paid_at=?
        WHERE label=?
        AND status='pending'
        """,
        (
            operation_id,
            received_amount,
            withdraw_amount,
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

        WHERE orders.user_id=?

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
        SELECT COALESCE(
            SUM(amount),
            0
        )
        FROM orders
        WHERE status='paid'
        """
    ).fetchone()[0]

    commission = connection.execute(
        """
        SELECT COALESCE(
            SUM(commission),
            0
        )
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
        commission,
    )


# ============================================================
# BOT
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    ),
)

dp = Dispatcher()


# ============================================================
# HELPERS
# ============================================================

def is_admin(user_id: int):
    return user_id in ADMIN_IDS


async def safe_callback_answer(
    callback: CallbackQuery,
):
    try:
        await callback.answer()
    except Exception:
        pass


# ============================================================
# KEYBOARDS
# ============================================================

def main_menu():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛍 Каталог",
                    callback_data="catalog",
                ),
                InlineKeyboardButton(
                    text="📦 Мои покупки",
                    callback_data="purchases",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✨ Как это работает",
                    callback_data="how",
                ),
                InlineKeyboardButton(
                    text="💬 Поддержка",
                    callback_data="support",
                ),
            ],
        ]
    )


def back_home_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏠 Главное меню",
                    callback_data="home",
                )
            ]
        ]
    )


def product_keyboard(
    product_id: int,
):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Купить сейчас",
                    callback_data=(
                        f"buy:{product_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад к каталогу",
                    callback_data="catalog",
                )
            ],
        ]
    )


def payment_keyboard(
    payment_url: str,
    label: str,
):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 ОПЛАТИТЬ",
                    url=payment_url,
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Проверить оплату",
                    callback_data=(
                        f"check:{label}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data="catalog",
                )
            ],
        ]
    )


def admin_menu():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Новый товар",
                    callback_data="admin_add",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 Товары",
                    callback_data="admin_products",
                ),
                InlineKeyboardButton(
                    text="📊 Статистика",
                    callback_data="admin_stats",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Магазин",
                    callback_data="home",
                )
            ],
        ]
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

    text = (
        "╭──────────────────────╮\n"
        f"│  ✦ <b>{SHOP_NAME}</b>\n"
        "╰──────────────────────╯\n\n"

        "Добро пожаловать в магазин цифровых товаров.\n\n"

        "📁 <b>Мгновенная выдача</b>\n"
        "💳 <b>Безопасная оплата</b>\n"
        "⚡ <b>Автоматическая доставка</b>\n"
        "🔐 <b>Уникальный заказ</b>\n\n"

        "<i>Выберите нужный раздел ниже.</i>"
    )

    await message.answer(
        text,
        reply_markup=main_menu(),
    )


# ============================================================
# HOME
# ============================================================

@dp.callback_query(F.data == "home")
async def home(
    callback: CallbackQuery,
):

    text = (
        "╭──────────────────────╮\n"
        f"│  ✦ <b>{SHOP_NAME}</b>\n"
        "╰──────────────────────╯\n\n"

        "Цифровые товары с автоматической выдачей.\n\n"

        "📁 Файл отправляется прямо в Telegram.\n"
        "💳 Оплата проходит через ЮMoney.\n"
        "⚡ После подтверждения платежа товар "
        "выдаётся автоматически.\n\n"

        "<i>Выберите раздел:</i>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=main_menu(),
    )

    await safe_callback_answer(callback)


# ============================================================
# CATALOG
# ============================================================

@dp.callback_query(F.data == "catalog")
async def catalog(
    callback: CallbackQuery,
):

    products = get_products()

    if not products:

        await callback.message.edit_text(
            "╭──────────────────────╮\n"
            "│  🛍 <b>КАТАЛОГ</b>\n"
            "╰──────────────────────╯\n\n"
            "Сейчас товаров нет.\n\n"
            "Загляните позже.",
            reply_markup=main_menu(),
        )

        await safe_callback_answer(callback)
        return

    buttons = []

    for product in products:

        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"📄 {product['name']} "
                        f"· {product['price']:.2f} ₽"
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
                text="🏠 Главное меню",
                callback_data="home",
            )
        ]
    )

    await callback.message.edit_text(
        "╭──────────────────────╮\n"
        "│  🛍 <b>КАТАЛОГ</b>\n"
        "╰──────────────────────╯\n\n"
        "Выберите цифровой товар:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await safe_callback_answer(callback)


# ============================================================
# PRODUCT
# ============================================================

@dp.callback_query(
    F.data.startswith("product:")
)
async def product_view(
    callback: CallbackQuery,
):

    product_id = int(
        callback.data.split(":")[1]
    )

    product = get_product(product_id)

    if not product:

        await callback.answer(
            "Товар больше недоступен.",
            show_alert=True,
        )
        return

    customer_amount = calculate_customer_amount(
        float(product["price"])
    )

    commission = calculate_fee(
        customer_amount,
        float(product["price"]),
    )

    text = (
        "╭──────────────────────╮\n"
        "│  📄 <b>ТОВАР</b>\n"
        "╰──────────────────────╯\n\n"

        f"<b>{product['name']}</b>\n\n"

        f"{product['description']}\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        f"💰 Цена товара: "
        f"<b>{product['price']:.2f} ₽</b>\n"

        f"💳 С учётом комиссии: "
        f"<b>{customer_amount:.2f} ₽</b>\n"

        f"⚡ Комиссия: "
        f"<b>{commission:.2f} ₽</b>\n\n"

        "📁 После подтверждения оплаты "
        "файл будет отправлен автоматически."
    )

    await callback.message.edit_text(
        text,
        reply_markup=product_keyboard(
            product_id
        ),
    )

    await safe_callback_answer(callback)


# ============================================================
# BUY
# ============================================================

@dp.callback_query(
    F.data.startswith("buy:")
)
async def buy(
    callback: CallbackQuery,
):

    user = callback.from_user

    if not user:
        return

    product_id = int(
        callback.data.split(":")[1]
    )

    product = get_product(product_id)

    if not product:

        await callback.answer(
            "Товар не найден.",
            show_alert=True,
        )
        return

    (
        order_id,
        label,
        customer_amount,
        commission,
    ) = create_order(
        user_id=user.id,
        username=user.username,
        product_id=product_id,
        product_price=float(
            product["price"]
        ),
    )

    payment_url = (
        "https://yoomoney.ru/quickpay/confirm?"
        + urllib.parse.urlencode(
            {
                "receiver": YOOMONEY_WALLET,
                "quickpay-form": "button",
                "sum": (
                    f"{customer_amount:.2f}"
                ),
                "paymentType": "AC",
                "label": label,
            }
        )
    )

    text = (
        "╭──────────────────────╮\n"
        "│  💳 <b>ОПЛАТА</b>\n"
        "╰──────────────────────╯\n\n"

        f"📦 <b>{product['name']}</b>\n\n"

        f"🏷 Стоимость: "
        f"<b>{product['price']:.2f} ₽</b>\n"

        f"💳 К оплате: "
        f"<b>{customer_amount:.2f} ₽</b>\n"

        f"⚙️ Комиссия: "
        f"<b>{commission:.2f} ₽</b>\n\n"

        f"🧾 Заказ: "
        f"<code>#{order_id}</code>\n\n"

        "Нажмите кнопку ниже и завершите оплату.\n\n"

        "После оплаты нажмите "
        "<b>«Проверить оплату»</b> — "
        "бот также автоматически обработает "
        "уведомление ЮMoney."
    )

    await callback.message.edit_text(
        text,
        reply_markup=payment_keyboard(
            payment_url,
            label,
        ),
    )

    await safe_callback_answer(callback)


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

    if (
        order["user_id"]
        != callback.from_user.id
    ):

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
        "Если ты только что оплатил — "
        "подожди несколько секунд и попробуй снова.",
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
            "╭──────────────────────╮\n"
            "│  📦 <b>МОИ ПОКУПКИ</b>\n"
            "╰──────────────────────╯\n\n"
            "У вас пока нет заказов.",
            reply_markup=main_menu(),
        )

        await safe_callback_answer(callback)
        return

    text = (
        "╭──────────────────────╮\n"
        "│  📦 <b>МОИ ПОКУПКИ</b>\n"
        "╰──────────────────────╯\n\n"
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
            f"💰 {row['amount']:.2f} ₽\n"
            f"{status}\n"
            f"🧾 <code>{row['label']}</code>\n\n"
        )

    await callback.message.edit_text(
        text,
        reply_markup=main_menu(),
    )

    await safe_callback_answer(callback)


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
        "╭──────────────────────╮\n"
        "│  ✨ <b>КАК ЭТО РАБОТАЕТ</b>\n"
        "╰──────────────────────╯\n\n"

        "① 🛍 <b>Выберите товар</b>\n"
        "Откройте каталог и выберите нужный файл.\n\n"

        "② 💳 <b>Оплатите</b>\n"
        "Бот создаст уникальный заказ и "
        "отправит вас на страницу ЮMoney.\n\n"

        "③ 🔐 <b>Платёж проверяется</b>\n"
        "ЮMoney отправляет боту уведомление "
        "с уникальной меткой заказа.\n\n"

        "④ 📁 <b>Получите файл</b>\n"
        "После успешной проверки бот автоматически "
        "отправит купленный файл.\n\n"

        "⚡ Всё происходит автоматически."
    )

    await callback.message.edit_text(
        text,
        reply_markup=main_menu(),
    )

    await safe_callback_answer(callback)


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
        "╭──────────────────────╮\n"
        "│  💬 <b>ПОДДЕРЖКА</b>\n"
        "╰──────────────────────╯\n\n"

        "Возникла проблема с оплатой "
        "или получением товара?\n\n"

        "Перед обращением убедитесь, что:\n\n"
        "• платёж действительно завершён;\n"
        "• вы нажали «Проверить оплату»;\n"
        "• прошло несколько секунд после оплаты.\n\n"

        "Если проблема осталась — "
        "обратитесь к администратору магазина."
    )

    await callback.message.edit_text(
        text,
        reply_markup=main_menu(),
    )

    await safe_callback_answer(callback)


# ============================================================
# ADMIN
# ============================================================

@dp.message(Command("admin"))
async def admin(
    message: Message,
):

    if not message.from_user:
        return

    if not is_admin(
        message.from_user.id
    ):

        await message.answer(
            "⛔ Доступ запрещён."
        )
        return

    await message.answer(
        "╭──────────────────────╮\n"
        "│  ⚙️ <b>ADMIN PANEL</b>\n"
        "╰──────────────────────╯\n\n"
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
        "╭──────────────────────╮\n"
        "│  ⚙️ <b>ADMIN PANEL</b>\n"
        "╰──────────────────────╯\n\n"
        "Выберите действие:",
        reply_markup=admin_menu(),
    )

    await safe_callback_answer(callback)


# ============================================================
# ADMIN STATS
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
        commission,
    ) = statistics()

    net_revenue = (
        revenue
        - commission
    )

    text = (
        "╭──────────────────────╮\n"
        "│  📊 <b>СТАТИСТИКА</b>\n"
        "╰──────────────────────╯\n\n"

        f"👥 Пользователей: "
        f"<b>{users}</b>\n"

        f"📦 Товаров: "
        f"<b>{products}</b>\n"

        f"🧾 Заказов: "
        f"<b>{orders}</b>\n"

        f"✅ Оплачено: "
        f"<b>{paid_orders}</b>\n\n"

        f"💰 Продано на: "
        f"<b>{revenue:.2f} ₽</b>\n"

        f"⚙️ Комиссия: "
        f"<b>{commission:.2f} ₽</b>\n"

        f"💎 После комиссии: "
        f"<b>{net_revenue:.2f} ₽</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=admin_menu(),
    )

    await safe_callback_answer(callback)


# ============================================================
# ADMIN PRODUCTS
# ============================================================

def admin_products_keyboard():

    rows = []

    products = get_products()

    for product in products:

        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"🗑 {product['name']} "
                        f"· {product['price']:.2f} ₽"
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
                text="➕ Добавить товар",
                callback_data="admin_add",
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Панель",
                callback_data="admin",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


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
            "╭──────────────────────╮\n"
            "│  📦 <b>ТОВАРЫ</b>\n"
            "╰──────────────────────╯\n\n"
            "Товаров пока нет."
        )

    else:

        text = (
            "╭──────────────────────╮\n"
            "│  📦 <b>ТОВАРЫ</b>\n"
            "╰──────────────────────╯\n\n"
        )

        for product in products:

            text += (
                f"#{product['id']} · "
                f"<b>{product['name']}</b>\n"
                f"💰 {product['price']:.2f} ₽\n\n"
            )

    await callback.message.edit_text(
        text,
        reply_markup=admin_products_keyboard(),
    )

    await safe_callback_answer(callback)


# ============================================================
# ADMIN ADD
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
        "╭──────────────────────╮\n"
        "│  ✨ <b>НОВЫЙ ТОВАР</b>\n"
        "╰──────────────────────╯\n\n"

        "📌 <b>Шаг 1 из 4</b>\n\n"

        "Введите название товара.\n\n"

        "Например:\n"
        "<code>Премиум инструкция</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✕ Отмена",
                        callback_data="admin",
                    )
                ]
            ]
        ),
    )

    await safe_callback_answer(callback)


# ============================================================
# ADMIN TEXT INPUT
# ============================================================

@dp.message(F.text)
async def admin_text_input(
    message: Message,
):

    if not message.from_user:
        return

    user_id = message.from_user.id

    if not is_admin(user_id):
        return

    state = admin_states.get(user_id)

    if not state:
        return

    text = message.text.strip()

    # --------------------------------------------------------
    # NAME
    # --------------------------------------------------------

    if state["step"] == "name":

        if len(text) > 100:

            await message.answer(
                "⚠️ Название слишком длинное.\n"
                "Максимум — 100 символов."
            )
            return

        state["name"] = text
        state["step"] = "description"

        await message.answer(
            "📝 <b>Шаг 2 из 4</b>\n\n"
            "Введите описание товара."
        )

        return

    # --------------------------------------------------------
    # DESCRIPTION
    # --------------------------------------------------------

    if state["step"] == "description":

        if len(text) > 4000:

            await message.answer(
                "⚠️ Описание слишком длинное.\n"
                "Максимум — 4000 символов."
            )
            return

        state["description"] = text
        state["step"] = "price"

        await message.answer(
            "💰 <b>Шаг 3 из 4</b>\n\n"
            "Введите цену товара в рублях.\n\n"
            "Например:\n"
            "<code>10</code>"
        )

        return

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    if state["step"] == "price":

        try:

            price = float(
                text.replace(",", ".")
            )

            if price <= 0:
                raise ValueError

            if price > 1_000_000:
                raise ValueError

        except Exception:

            await message.answer(
                "❌ Неверная цена.\n\n"
                "Например:\n"
                "<code>10</code>\n"
                "<code>499.90</code>"
            )
            return

        state["price"] = round(
            price,
            2,
        )

        state["step"] = "file"

        await message.answer(
            "📁 <b>Шаг 4 из 4</b>\n\n"
            "Теперь отправь файл "
            "<b>как документ Telegram</b>.\n\n"
            "После загрузки товар автоматически "
            "появится в каталоге."
        )

        return


# ============================================================
# ADMIN FILE
# ============================================================

@dp.message(F.document)
async def admin_document_input(
    message: Message,
):

    if not message.from_user:
        return

    user_id = message.from_user.id

    if not is_admin(user_id):
        return

    state = admin_states.get(user_id)

    if not state:
        return

    if state.get("step") != "file":

        await message.answer(
            "⚠️ Сейчас бот не ожидает файл."
        )
        return

    document = message.document

    original_name = (
        document.file_name
        or "product_file"
    )

    status = await message.answer(
        "⏳ <b>Загружаю файл...</b>\n\n"
        f"📄 <code>{original_name}</code>"
    )

    destination = None

    try:

        tg_file = await bot.get_file(
            document.file_id
        )

        if not tg_file.file_path:
            raise RuntimeError(
                "Telegram не вернул путь файла."
            )

        extension = Path(
            original_name
        ).suffix

        safe_filename = (
            secrets.token_hex(16)
            + extension
        )

        destination = (
            FILES_DIR
            / safe_filename
        )

        await bot.download_file(
            tg_file.file_path,
            destination=destination,
        )

        if not destination.exists():

            raise RuntimeError(
                "Файл не сохранился."
            )

        file_size = destination.stat().st_size

        if file_size <= 0:

            destination.unlink(
                missing_ok=True
            )

            raise RuntimeError(
                "Файл имеет нулевой размер."
            )

        product_id = add_product(
            name=state["name"],
            description=state["description"],
            price=state["price"],
            file_path=str(destination),
            original_filename=original_name,
        )

        product_name = state["name"]
        product_price = state["price"]

        del admin_states[user_id]

        await status.edit_text(
            "╭──────────────────────╮\n"
            "│  ✨ <b>ТОВАР СОЗДАН</b>\n"
            "╰──────────────────────╯\n\n"

            "✅ Товар успешно добавлен.\n\n"

            f"🆔 ID: <code>{product_id}</code>\n"
            f"📦 <b>{product_name}</b>\n"
            f"💰 <b>{product_price:.2f} ₽</b>\n"
            f"📁 <code>{original_name}</code>\n\n"

            "🟢 Теперь товар доступен "
            "в каталоге.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📦 Товары",
                            callback_data=(
                                "admin_products"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="➕ Добавить ещё",
                            callback_data=(
                                "admin_add"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="⚙️ Панель",
                            callback_data="admin",
                        )
                    ],
                ]
            ),
        )

    except Exception as error:

        logger.exception(
            "File upload error: %s",
            error,
        )

        if destination:

            try:
                destination.unlink(
                    missing_ok=True
                )
            except Exception:
                pass

        await status.edit_text(
            "╭──────────────────────╮\n"
            "│  ❌ <b>ОШИБКА</b>\n"
            "╰──────────────────────╯\n\n"

            "Не удалось сохранить файл.\n\n"

            "Попробуй отправить файл ещё раз.\n\n"

            f"<code>{str(error)[:500]}</code>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔄 Повторить",
                            callback_data=(
                                "admin_add"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="⚙️ Панель",
                            callback_data="admin",
                        )
                    ],
                ]
            ),
        )


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

    product_id = int(
        callback.data.split(":")[1]
    )

    product = get_product(product_id)

    if not product:

        await callback.answer(
            "Товар уже удалён.",
            show_alert=True,
        )
        return

    delete_product(product_id)

    try:

        path = Path(
            product["file_path"]
        )

        path.unlink(
            missing_ok=True
        )

    except Exception:

        logger.exception(
            "Could not delete product file"
        )

    await callback.message.edit_text(
        "╭──────────────────────╮\n"
        "│  🗑 <b>ТОВАР УДАЛЁН</b>\n"
        "╰──────────────────────╯\n\n"
        f"Товар <b>{product['name']}</b> "
        "удалён из магазина.",
        reply_markup=admin_products_keyboard(),
    )

    await safe_callback_answer(callback)


# ============================================================
# YOUMONEY SIGN
# ============================================================

def verify_yoomoney_signature(
    data: dict,
) -> bool:

    received_sign = data.get("sign")

    if not received_sign:
        return False

    params = {}

    for key, value in data.items():

        if key == "sign":
            continue

        if isinstance(value, list):
            value = value[0]

        params[key] = str(value)

    sorted_items = sorted(
        params.items(),
        key=lambda item: item[0],
    )

    prepared_parts = []

    for key, value in sorted_items:

        encoded_key = urllib.parse.quote(
            str(key),
            safe="-_.~",
        )

        encoded_value = urllib.parse.quote(
            str(value),
            safe="-_.~",
        )

        prepared_parts.append(
            f"{encoded_key}={encoded_value}"
        )

    prepared = "&".join(
        prepared_parts
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
        str(received_sign).lower(),
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

        logger.error(
            "Product file missing: %s",
            file_path,
        )

        await bot.send_message(
            user_id,
            "⚠️ Оплата подтверждена.\n\n"
            "Однако файл товара временно "
            "недоступен.\n\n"
            "Администратор уведомлён.",
        )

        return False

    await bot.send_message(
        user_id,
        "╭──────────────────────╮\n"
        "│  🎉 <b>ОПЛАТА ПОЛУЧЕНА</b>\n"
        "╰──────────────────────╯\n\n"

        f"📦 <b>{product['name']}</b>\n\n"

        "✅ Платёж подтверждён.\n"
        "📁 Ваш файл уже готов.\n\n"

        "Спасибо за покупку ❤️",
    )

    await bot.send_document(
        user_id,
        FSInputFile(
            path=file_path,
            filename=(
                product["original_filename"]
                or file_path.name
            ),
        ),
        caption=(
            f"📄 <b>{product['name']}</b>\n\n"
            "Спасибо за покупку ❤️"
        ),
    )

    return True


# ============================================================
# YOUMONEY WEBHOOK
# ============================================================

async def yoomoney_webhook(
    request: web.Request,
):

    try:

        post = await request.post()

        data = {
            key: value
            for key, value in post.items()
        }

        logger.info(
            "YuMoney notification: %s",
            data,
        )

        # ----------------------------------------------------
        # SIGNATURE
        # ----------------------------------------------------

        if not verify_yoomoney_signature(
            data
        ):

            logger.warning(
                "Invalid YuMoney signature"
            )

            return web.Response(
                status=403,
                text="invalid signature",
            )

        # ----------------------------------------------------
        # NOTIFICATION TYPE
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # CURRENCY
        # ----------------------------------------------------

        currency = str(
            data.get(
                "currency",
                "",
            )
        )

        if currency != "643":

            logger.warning(
                "Wrong currency: %s",
                currency,
            )

            return web.Response(
                status=200,
                text="wrong currency",
            )

        # ----------------------------------------------------
        # LABEL
        # ----------------------------------------------------

        label = str(
            data.get(
                "label",
                "",
            )
        ).strip()

        if not label:

            logger.warning(
                "Notification without label"
            )

            return web.Response(
                status=200,
                text="no label",
            )

        order = get_order_by_label(
            label
        )

        if not order:

            logger.warning(
                "Unknown label: %s",
                label,
            )

            return web.Response(
                status=200,
                text="unknown order",
            )

        # ----------------------------------------------------
        # ALREADY PAID
        # ----------------------------------------------------

        if order["status"] == "paid":

            logger.info(
                "Order already paid: %s",
                label,
            )

            return web.Response(
                status=200,
                text="already paid",
            )

        # ----------------------------------------------------
        # OPERATION ID
        # ----------------------------------------------------

        operation_id = str(
            data.get(
                "operation_id",
                "",
            )
        ).strip()

        if not operation_id:

            logger.warning(
                "Payment without operation_id"
            )

            return web.Response(
                status=200,
                text="no operation id",
            )

        # ----------------------------------------------------
        # REPLAY PROTECTION
        # ----------------------------------------------------

        existing_operation = (
            get_order_by_operation(
                operation_id
            )
        )

        if existing_operation:

            logger.warning(
                "Operation already used: %s",
                operation_id,
            )

            return web.Response(
                status=200,
                text="operation already used",
            )

        # ----------------------------------------------------
        # AMOUNT
        # ----------------------------------------------------

        try:

            received_amount = round(
                float(
                    str(
                        data.get(
                            "amount",
                            "0",
                        )
                    ).replace(",", ".")
                ),
                2,
            )

        except Exception:

            received_amount = 0.0

        # ----------------------------------------------------
        # WITHDRAW AMOUNT
        # ----------------------------------------------------

        try:

            withdraw_amount = round(
                float(
                    str(
                        data.get(
                            "withdraw_amount",
                            "0",
                        )
                    ).replace(",", ".")
                ),
                2,
            )

        except Exception:

            withdraw_amount = 0.0

        expected_net = round(
            float(order["amount"]),
            2,
        )

        expected_customer = round(
            float(order["customer_amount"]),
            2,
        )

        # ----------------------------------------------------
        # MAIN PAYMENT CHECK
        #
        # amount = зачислено тебе
        #
        # withdraw_amount =
        # списано с покупателя
        #
        # ----------------------------------------------------

        net_ok = (
            received_amount
            + MONEY_EPSILON
            >= expected_net
        )

        customer_ok = (
            withdraw_amount
            + MONEY_EPSILON
            >= expected_customer
        )

        logger.info(
            "Payment check | "
            "label=%s | "
            "received=%.2f | "
            "expected_net=%.2f | "
            "withdraw=%.2f | "
            "expected_customer=%.2f | "
            "net_ok=%s | "
            "customer_ok=%s",
            label,
            received_amount,
            expected_net,
            withdraw_amount,
            expected_customer,
            net_ok,
            customer_ok,
        )

        # ----------------------------------------------------
        # ПРОВЕРЯЕМ ЗАЧИСЛЕНИЕ
        # ----------------------------------------------------

        if not net_ok:

            logger.warning(
                "Insufficient credited amount"
            )

            await bot.send_message(
                order["user_id"],
                "⚠️ <b>Платёж получен, "
                "но сумма недостаточна.</b>\n\n"

                f"Зачислено: "
                f"<b>{received_amount:.2f} ₽</b>\n"

                f"Нужно: "
                f"<b>{expected_net:.2f} ₽</b>\n\n"

                "Товар пока не выдан."
            )

            return web.Response(
                status=200,
                text="insufficient credited amount",
            )

        # ----------------------------------------------------
        # ПРОВЕРЯЕМ СУММУ, СПИСАННУЮ У ПОКУПАТЕЛЯ
        # ----------------------------------------------------

        if (
            withdraw_amount > 0
            and not customer_ok
        ):

            logger.warning(
                "Withdraw amount mismatch"
            )

            await bot.send_message(
                order["user_id"],
                "⚠️ <b>Сумма платежа "
                "отличается от заказа.</b>\n\n"

                f"Списано: "
                f"<b>{withdraw_amount:.2f} ₽</b>\n"

                f"Ожидалось: "
                f"<b>{expected_customer:.2f} ₽</b>\n\n"

                "Товар пока не выдан."
            )

            return web.Response(
                status=200,
                text="withdraw amount mismatch",
            )

        # ----------------------------------------------------
        # MARK PAID
        # ----------------------------------------------------

        paid_order = mark_order_paid(
            label=label,
            operation_id=operation_id,
            received_amount=received_amount,
            withdraw_amount=withdraw_amount,
        )

        if not paid_order:

            return web.Response(
                status=200,
                text="already processed",
            )

        # ----------------------------------------------------
        # PRODUCT
        # ----------------------------------------------------

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
                "⚠️ Платёж подтверждён, "
                "но товар не найден.\n\n"
                "Администратор уведомлён.",
            )

            return web.Response(
                status=200,
                text="product missing",
            )

        # ----------------------------------------------------
        # SEND PRODUCT
        # ----------------------------------------------------

        try:

            sent = await send_product_to_user(
                paid_order["user_id"],
                product,
            )

            if not sent:

                logger.error(
                    "Product could not be sent."
                )

        except Exception as error:

            logger.exception(
                "Product delivery error: %s",
                error,
            )

            await bot.send_message(
                paid_order["user_id"],
                "⚠️ Платёж подтверждён, "
                "но при отправке файла произошла ошибка.\n\n"
                "Администратор уведомлён.",
            )

        # ----------------------------------------------------
        # ADMIN NOTIFICATION
        # ----------------------------------------------------

        commission = calculate_fee(
            received_amount,
            expected_net,
        )

        for admin_id in ADMIN_IDS:

            try:

                await bot.send_message(
                    admin_id,
                    "╭──────────────────────╮\n"
                    "│  💰 <b>НОВАЯ ОПЛАТА</b>\n"
                    "╰──────────────────────╯\n\n"

                    f"🧾 Заказ: "
                    f"<code>#{paid_order['id']}</code>\n"

                    f"📦 Товар: "
                    f"<b>{product['name']}</b>\n\n"

                    f"💳 Списано: "
                    f"<b>{withdraw_amount:.2f} ₽</b>\n"

                    f"💰 Зачислено: "
                    f"<b>{received_amount:.2f} ₽</b>\n"

                    f"⚙️ Комиссия: "
                    f"<b>{commission:.2f} ₽</b>\n\n"

                    f"👤 User ID: "
                    f"<code>{paid_order['user_id']}</code>\n"

                    f"🔑 Label: "
                    f"<code>{label}</code>\n"

                    f"🆔 Operation: "
                    f"<code>{operation_id}</code>"
                )

            except Exception:

                logger.exception(
                    "Admin notification error"
                )

        return web.Response(
            status=200,
            text="ok",
        )

    except Exception as error:

        logger.exception(
            "YuMoney webhook error: %s",
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
            status=200,
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
        text="SEVAN MARKET is running.",
    )


# ============================================================
# HTTP SERVER
# ============================================================

async def start_web_server():

    app = web.Application()

    app.router.add_get(
        "/",
        health,
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
        port=WEB_PORT,
    )

    await site.start()

    logger.info(
        "HTTP server started on port %s",
        WEB_PORT,
    )

    return runner


# ============================================================
# VALIDATE CONFIG
# ============================================================

def validate_config():

    if (
        not BOT_TOKEN
        or BOT_TOKEN.startswith(
            "PASTE_"
        )
    ):

        raise RuntimeError(
            "BOT_TOKEN не задан."
        )

    if (
        not YOOMONEY_WALLET
        or YOOMONEY_WALLET.startswith(
            "PASTE_"
        )
    ):

        raise RuntimeError(
            "YOOMONEY_WALLET не задан."
        )

    if (
        not YOOMONEY_SECRET
        or YOOMONEY_SECRET.startswith(
            "PASTE_"
        )
    ):

        raise RuntimeError(
            "YOOMONEY_SECRET не задан."
        )

    if (
        not WEBHOOK_BASE_URL
        or "YOUR_DOMAIN" in WEBHOOK_BASE_URL
    ):

        raise RuntimeError(
            "WEBHOOK_BASE_URL не задан."
        )

    if not ADMIN_IDS:

        raise RuntimeError(
            "ADMIN_IDS пуст."
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    validate_config()

    init_db()

    web_runner = await start_web_server()

    try:

        # ----------------------------------------------------
        # TELEGRAM WEBHOOK
        # ----------------------------------------------------

        await bot.set_webhook(
            url=TELEGRAM_WEBHOOK_URL,
            drop_pending_updates=True,
        )

        logger.info(
            "Telegram webhook URL: %s",
            TELEGRAM_WEBHOOK_URL,
        )

        logger.info(
            "================================="
        )

        logger.info(
            "%s started successfully",
            SHOP_NAME,
        )

        logger.info(
            "Telegram webhook: %s",
            TELEGRAM_WEBHOOK_URL,
        )

        logger.info(
            "YuMoney webhook: %s",
            YOOMONEY_WEBHOOK_URL,
        )

        logger.info(
            "YuMoney commission rate: %.2f%%",
            YOOMONEY_FEE_RATE * 100,
        )

        logger.info(
            "================================="
        )

        # ----------------------------------------------------
        # KEEP RUNNING
        # ----------------------------------------------------

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

        asyncio.run(main())

    except KeyboardInterrupt:

        logger.info(
            "SEVAN MARKET stopped."
        )
