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
# SEVAN MARKET
# Telegram Digital Store
#
# Python 3.12+
#
# requirements.txt:
#
# aiogram>=3.30.0,<4
# aiohttp>=3.11,<4
#
# Render:
#
# Start Command:
# python bot.py
#
# Environment Variables:
#
# BOT_TOKEN
# YOOMONEY_WALLET
# YOOMONEY_SECRET
# WEBHOOK_BASE_URL
#
# ADMIN_IDS
#
# Example:
#
# BOT_TOKEN=...
# YOOMONEY_WALLET=41001...
# YOOMONEY_SECRET=...
# WEBHOOK_BASE_URL=https://your-app.onrender.com
# ADMIN_IDS=8346538289
#
# ============================================================


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

SHOP_NAME = "SEVAN MARKET"

CURRENCY = "₽"

PORT = int(
    os.getenv(
        "PORT",
        "10000",
    )
)

DB_FILE = "store.db"

FILES_DIR = Path("files")
FILES_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


def load_admin_ids():
    raw = os.getenv(
        "ADMIN_IDS",
        "8346538289",
    )

    result = set()

    for item in raw.split(","):
        item = item.strip()

        if not item:
            continue

        try:
            result.add(int(item))
        except ValueError:
            pass

    return result


ADMIN_IDS = load_admin_ids()


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
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():
    conn = db()

    conn.execute(
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

    conn.execute(
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

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()

    logger.info("Database initialized")


# ============================================================
# USERS
# ============================================================

def save_user(
    user_id: int,
    username: str | None,
    first_name: str | None,
):
    conn = db()

    conn.execute(
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

    conn.commit()
    conn.close()


# ============================================================
# PRODUCTS
# ============================================================

def get_products():
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM products
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    return rows


def get_product(product_id: int):
    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM products
        WHERE id=?
        """,
        (product_id,),
    ).fetchone()

    conn.close()

    return row


def add_product(
    name: str,
    description: str,
    price: float,
    file_path: str,
    original_filename: str,
):
    conn = db()

    cursor = conn.execute(
        """
        INSERT INTO products (
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

    conn.commit()
    conn.close()

    return product_id


def delete_product(product_id: int):
    conn = db()

    conn.execute(
        """
        DELETE FROM products
        WHERE id=?
        """,
        (product_id,),
    )

    conn.commit()
    conn.close()


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
        "SEVAN-"
        + secrets.token_hex(8).upper()
    )

    conn = db()

    cursor = conn.execute(
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
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
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

    conn.commit()
    conn.close()

    return order_id, label


def get_order_by_label(label: str):
    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM orders
        WHERE label=?
        """,
        (label,),
    ).fetchone()

    conn.close()

    return row


def mark_order_paid(
    label: str,
    operation_id: str,
):
    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM orders
        WHERE label=?
        AND status='pending'
        """,
        (label,),
    ).fetchone()

    if not row:
        conn.close()
        return None

    conn.execute(
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
            utc_now(),
            label,
        ),
    )

    conn.commit()
    conn.close()

    return row


def get_user_orders(user_id: int):
    conn = db()

    rows = conn.execute(
        """
        SELECT
            orders.*,
            products.name AS product_name

        FROM orders

        LEFT JOIN products
            ON products.id=orders.product_id

        WHERE orders.user_id=?

        ORDER BY orders.id DESC
        """,
        (user_id,),
    ).fetchall()

    conn.close()

    return rows


# ============================================================
# STATISTICS
# ============================================================

def statistics():
    conn = db()

    users = conn.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    products = conn.execute(
        "SELECT COUNT(*) FROM products"
    ).fetchone()[0]

    orders = conn.execute(
        "SELECT COUNT(*) FROM orders"
    ).fetchone()[0]

    paid = conn.execute(
        """
        SELECT COUNT(*)
        FROM orders
        WHERE status='paid'
        """
    ).fetchone()[0]

    revenue = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM orders
        WHERE status='paid'
        """
    ).fetchone()[0]

    conn.close()

    return (
        users,
        products,
        orders,
        paid,
        revenue,
    )


# ============================================================
# TELEGRAM
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
    ),
)

dp = Dispatcher()


# ============================================================
# BUTTON HELPER
#
# Telegram supports:
# primary = blue
# success = green
# danger  = red
# ============================================================

def btn(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    style: str | None = None,
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
                btn(
                    "🛍  Каталог",
                    callback_data="catalog",
                    style="primary",
                ),
            ],
            [
                btn(
                    "📦  Мои покупки",
                    callback_data="purchases",
                    style="primary",
                ),
            ],
            [
                btn(
                    "💎  Как это работает",
                    callback_data="how",
                    style="primary",
                ),
                btn(
                    "💬  Поддержка",
                    callback_data="support",
                    style="primary",
                ),
            ],
        ]
    )


# ============================================================
# HOME
# ============================================================

def home_text():
    return (
        "✨ <b>SEVAN MARKET</b>\n\n"
        "Цифровые товары с мгновенной доставкой.\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📁 <b>Цифровые товары</b>\n"
        "⚡ <b>Мгновенная выдача</b>\n"
        "🔐 <b>Безопасная оплата</b>\n"
        "💳 <b>ЮMoney</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Выберите нужный раздел ниже.</i>"
    )


# ============================================================
# CATALOG
# ============================================================

def catalog_keyboard():
    products = get_products()

    rows = []

    for product in products:
        rows.append(
            [
                btn(
                    (
                        f"📄  {product['name']}"
                        f"   •   "
                        f"{product['price']:.2f} ₽"
                    ),
                    callback_data=(
                        f"product:{product['id']}"
                    ),
                    style="primary",
                )
            ]
        )

    rows.append(
        [
            btn(
                "🏠  Главное меню",
                callback_data="home",
                style="primary",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def catalog_text():
    products = get_products()

    if not products:
        return (
            "🛍 <b>КАТАЛОГ</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Сейчас каталог пуст.\n\n"
            "Мы уже работаем над новыми "
            "цифровыми товарами.\n\n"
            "━━━━━━━━━━━━━━━━━━"
        )

    return (
        "🛍 <b>КАТАЛОГ</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите товар, чтобы открыть "
        "подробную информацию.\n\n"
        "⚡ После оплаты файл отправляется "
        "автоматически.\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# PRODUCT CARD
# ============================================================

def product_text(product):
    description = product["description"].strip()

    return (
        "📦 <b>ТОВАР</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>{product['name']}</b>\n\n"
        f"{description}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 Цена: <b>{product['price']:.2f} ₽</b>\n"
        "⚡ Выдача: <b>мгновенно</b>\n"
        "📁 Формат: <b>цифровой файл</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<i>После успешной оплаты файл "
        "будет отправлен сюда автоматически.</i>"
    )


def product_keyboard(product_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn(
                    "💳  КУПИТЬ",
                    callback_data=(
                        f"buy:{product_id}"
                    ),
                    style="success",
                ),
            ],
            [
                btn(
                    "🔙  Назад к каталогу",
                    callback_data="catalog",
                    style="primary",
                ),
            ],
        ]
    )


# ============================================================
# PAYMENT
# ============================================================

def create_yoomoney_url(
    amount: float,
    label: str,
):
    params = {
        "receiver": YOOMONEY_WALLET,
        "quickpay-form": "button",
        "sum": f"{amount:.2f}",
        "paymentType": "AC",
        "label": label,
    }

    return (
        "https://yoomoney.ru/quickpay/confirm?"
        + urllib.parse.urlencode(params)
    )


def payment_text(
    order_id: int,
    product,
):
    return (
        "💳 <b>ОПЛАТА ЗАКАЗА</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 <b>{product['name']}</b>\n\n"
        f"💰 К оплате: "
        f"<b>{product['price']:.2f} ₽</b>\n"
        f"🧾 Заказ: <code>#{order_id}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ Нажмите «Оплатить».\n"
        "2️⃣ Завершите оплату на ЮMoney.\n"
        "3️⃣ Вернитесь сюда.\n"
        "4️⃣ Бот автоматически выдаст файл.\n\n"
        "🔐 <i>Заказ привязан к вашему "
        "Telegram аккаунту.</i>"
    )


def payment_keyboard(
    payment_url: str,
    label: str,
):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn(
                    "💳  ОПЛАТИТЬ ЮMONEY",
                    url=payment_url,
                    style="success",
                ),
            ],
            [
                btn(
                    "🔄  ПРОВЕРИТЬ ОПЛАТУ",
                    callback_data=(
                        f"check:{label}"
                    ),
                    style="primary",
                ),
            ],
            [
                btn(
                    "🔙  Вернуться к товару",
                    callback_data=(
                        "back_payment_product:"
                        + label
                    ),
                    style="primary",
                ),
            ],
        ]
    )


# ============================================================
# PURCHASES
# ============================================================

def purchases_text(user_id: int):
    rows = get_user_orders(user_id)

    if not rows:
        return (
            "📦 <b>МОИ ПОКУПКИ</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "У вас пока нет заказов.\n\n"
            "Перейдите в каталог и выберите "
            "первый цифровой товар.\n\n"
            "━━━━━━━━━━━━━━━━━━"
        )

    text = (
        "📦 <b>МОИ ПОКУПКИ</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    for row in rows[:15]:

        if row["status"] == "paid":
            status = "🟢 Оплачен"
        else:
            status = "🟡 Ожидает оплаты"

        product_name = (
            row["product_name"]
            or "Удалённый товар"
        )

        text += (
            f"📄 <b>{product_name}</b>\n"
            f"💰 {row['amount']:.2f} ₽\n"
            f"{status}\n"
            f"🧾 <code>{row['label']}</code>\n\n"
        )

    text += (
        "━━━━━━━━━━━━━━━━━━"
    )

    return text


def purchases_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn(
                    "🛍  Открыть каталог",
                    callback_data="catalog",
                    style="primary",
                ),
            ],
            [
                btn(
                    "🏠  Главное меню",
                    callback_data="home",
                    style="primary",
                ),
            ],
        ]
    )


# ============================================================
# HOW
# ============================================================

def how_text():
    return (
        "💎 <b>КАК ЭТО РАБОТАЕТ</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "① 🛍 <b>Выберите товар</b>\n\n"
        "Откройте каталог и выберите "
        "нужный цифровой продукт.\n\n"
        "② 💳 <b>Оплатите</b>\n\n"
        "Бот создаст уникальный заказ "
        "и откроет страницу ЮMoney.\n\n"
        "③ 🔐 <b>Подтверждение</b>\n\n"
        "После получения уведомления "
        "ЮMoney бот проверит сумму "
        "и заказ.\n\n"
        "④ 📁 <b>Получите файл</b>\n\n"
        "После подтверждения оплаты "
        "файл автоматически отправится "
        "в этот чат.\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ <b>Без ожидания администратора.</b>"
    )


def how_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn(
                    "🛍  Перейти в каталог",
                    callback_data="catalog",
                    style="primary",
                ),
            ],
            [
                btn(
                    "🏠  Главное меню",
                    callback_data="home",
                    style="primary",
                ),
            ],
        ]
    )


# ============================================================
# SUPPORT
# ============================================================

def support_text():
    return (
        "💬 <b>ПОДДЕРЖКА</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Возникла проблема с оплатой "
        "или получением товара?\n\n"
        "Сохраните номер заказа и "
        "обратитесь к администратору.\n\n"
        "🧾 Номер заказа находится "
        "на странице оплаты и в разделе "
        "«Мои покупки».\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Мы постараемся решить проблему "
        "как можно быстрее.</i>"
    )


def support_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn(
                    "🏠  Главное меню",
                    callback_data="home",
                    style="primary",
                ),
            ],
        ]
    )


# ============================================================
# ANIMATION HELPERS
# ============================================================

async def loading(
    callback: CallbackQuery,
    text: str = "⏳ Загружаю...",
):
    try:
        await callback.message.edit_text(
            text,
        )
        await asyncio.sleep(0.18)
    except Exception:
        pass


async def safe_answer(
    callback: CallbackQuery,
):
    try:
        await callback.answer()
    except Exception:
        pass


# ============================================================
# START
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

    await message.answer(
        home_text(),
        reply_markup=main_menu(),
    )


# ============================================================
# HOME CALLBACK
# ============================================================

@dp.callback_query(F.data == "home")
async def home(
    callback: CallbackQuery,
):
    await safe_answer(callback)

    await loading(
        callback,
        "✨ <b>SEVAN MARKET</b>\n\n"
        "⏳ Открываю главное меню...",
    )

    await callback.message.edit_text(
        home_text(),
        reply_markup=main_menu(),
    )


# ============================================================
# CATALOG CALLBACK
# ============================================================

@dp.callback_query(F.data == "catalog")
async def catalog(
    callback: CallbackQuery,
):
    await safe_answer(callback)

    await loading(
        callback,
        "🛍 <b>КАТАЛОГ</b>\n\n"
        "⏳ Загружаю товары...",
    )

    products = get_products()

    if not products:
        await callback.message.edit_text(
            catalog_text(),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        btn(
                            "🏠  Главное меню",
                            callback_data="home",
                            style="primary",
                        )
                    ]
                ]
            ),
        )

        return

    await callback.message.edit_text(
        catalog_text(),
        reply_markup=catalog_keyboard(),
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
    await safe_answer(callback)

    try:
        product_id = int(
            callback.data.split(":")[1]
        )
    except Exception:
        return

    product = get_product(product_id)

    if not product:
        await callback.message.edit_text(
            "⚠️ <b>ТОВАР НЕ НАЙДЕН</b>\n\n"
            "Возможно, он был удалён.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        btn(
                            "🛍  Вернуться в каталог",
                            callback_data="catalog",
                            style="primary",
                        )
                    ]
                ]
            ),
        )

        return

    await callback.message.edit_text(
        product_text(product),
        reply_markup=product_keyboard(
            product_id
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
    if not callback.from_user:
        return

    await safe_answer(callback)

    try:
        product_id = int(
            callback.data.split(":")[1]
        )
    except Exception:
        return

    product = get_product(product_id)

    if not product:
        await callback.message.edit_text(
            "⚠️ <b>ТОВАР НЕДОСТУПЕН</b>\n\n"
            "Этот товар больше недоступен.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        btn(
                            "🛍  Каталог",
                            callback_data="catalog",
                            style="primary",
                        )
                    ]
                ]
            ),
        )

        return

    await loading(
        callback,
        "💳 <b>ОФОРМЛЕНИЕ ЗАКАЗА</b>\n\n"
        "⏳ Создаю заказ...",
    )

    order_id, label = create_order(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        product_id=product_id,
        amount=float(product["price"]),
    )

    payment_url = create_yoomoney_url(
        amount=float(product["price"]),
        label=label,
    )

    await callback.message.edit_text(
        payment_text(
            order_id,
            product,
        ),
        reply_markup=payment_keyboard(
            payment_url,
            label,
        ),
    )


# ============================================================
# CHECK PAYMENT
# ============================================================

@dp.callback_query(
    F.data.startswith("check:")
)
async def check_payment(
    callback: CallbackQuery,
):
    if not callback.from_user:
        return

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
        "Если вы только что оплатили — "
        "подождите несколько секунд.",
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
    if not callback.from_user:
        return

    await safe_answer(callback)

    await loading(
        callback,
        "📦 <b>МОИ ПОКУПКИ</b>\n\n"
        "⏳ Загружаю историю...",
    )

    await callback.message.edit_text(
        purchases_text(
            callback.from_user.id
        ),
        reply_markup=purchases_keyboard(),
    )


# ============================================================
# HOW
# ============================================================

@dp.callback_query(
    F.data == "how"
)
async def how(
    callback: CallbackQuery,
):
    await safe_answer(callback)

    await loading(
        callback,
        "💎 <b>КАК ЭТО РАБОТАЕТ</b>\n\n"
        "⏳ Загружаю информацию...",
    )

    await callback.message.edit_text(
        how_text(),
        reply_markup=how_keyboard(),
    )


# ============================================================
# SUPPORT
# ============================================================

@dp.callback_query(
    F.data == "support"
)
async def support(
    callback: CallbackQuery,
):
    await safe_answer(callback)

    await loading(
        callback,
        "💬 <b>ПОДДЕРЖКА</b>\n\n"
        "⏳ Открываю раздел...",
    )

    await callback.message.edit_text(
        support_text(),
        reply_markup=support_keyboard(),
    )


# ============================================================
# ADMIN
# ============================================================

def is_admin(
    user_id: int,
):
    return user_id in ADMIN_IDS


def admin_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn(
                    "➕  Добавить товар",
                    callback_data="admin:add",
                    style="success",
                ),
            ],
            [
                btn(
                    "📦  Товары",
                    callback_data="admin:products",
                    style="primary",
                ),
                btn(
                    "📊  Статистика",
                    callback_data="admin:stats",
                    style="primary",
                ),
            ],
            [
                btn(
                    "🏠  Магазин",
                    callback_data="home",
                    style="primary",
                ),
            ],
        ]
    )


def admin_text():
    return (
        "⚙️ <b>ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Управление магазином\n\n"
        "📦 Товары\n"
        "➕ Добавление товаров\n"
        "📊 Статистика продаж\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Выберите действие.</i>"
    )


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
            "⛔ <b>ДОСТУП ЗАПРЕЩЁН</b>\n\n"
            "У вас нет доступа к панели "
            "администратора."
        )

        return

    await message.answer(
        admin_text(),
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

    await safe_answer(callback)

    await callback.message.edit_text(
        admin_text(),
        reply_markup=admin_menu(),
    )


# ============================================================
# ADMIN PRODUCTS
# ============================================================

def admin_products_keyboard():
    products = get_products()

    rows = []

    for product in products:
        rows.append(
            [
                btn(
                    (
                        f"🗑  {product['name']}"
                    ),
                    callback_data=(
                        f"admin:delete:"
                        f"{product['id']}"
                    ),
                    style="danger",
                )
            ]
        )

    rows.append(
        [
            btn(
                "➕  Добавить товар",
                callback_data="admin:add",
                style="success",
            )
        ]
    )

    rows.append(
        [
            btn(
                "🔙  Назад",
                callback_data="admin",
                style="primary",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def admin_products_text():
    products = get_products()

    if not products:
        return (
            "📦 <b>ТОВАРЫ</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Каталог пока пуст.\n\n"
            "Добавьте первый товар.\n\n"
            "━━━━━━━━━━━━━━━━━━"
        )

    text = (
        "📦 <b>ТОВАРЫ</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    for product in products:
        text += (
            f"🆔 <code>#{product['id']}</code>\n"
            f"📄 <b>{product['name']}</b>\n"
            f"💰 {product['price']:.2f} ₽\n\n"
        )

    text += (
        "━━━━━━━━━━━━━━━━━━"
    )

    return text


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

    await safe_answer(callback)

    await loading(
        callback,
        "📦 <b>ТОВАРЫ</b>\n\n"
        "⏳ Загружаю каталог...",
    )

    await callback.message.edit_text(
        admin_products_text(),
        reply_markup=admin_products_keyboard(),
    )


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

    await safe_answer(callback)

    users, products, orders, paid, revenue = (
        statistics()
    )

    text = (
        "📊 <b>СТАТИСТИКА</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Пользователей: <b>{users}</b>\n\n"
        f"📦 Товаров: <b>{products}</b>\n\n"
        f"🧾 Заказов: <b>{orders}</b>\n\n"
        f"✅ Оплачено: <b>{paid}</b>\n\n"
        f"💰 Выручка: "
        f"<b>{revenue:.2f} ₽</b>\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn(
                    "🔄  Обновить",
                    callback_data="admin:stats",
                    style="primary",
                ),
            ],
            [
                btn(
                    "🔙  Назад",
                    callback_data="admin",
                    style="primary",
                ),
            ],
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
    )


# ============================================================
# ADMIN ADD PRODUCT
# ============================================================

admin_states = {}


def admin_add_text(step: str):
    if step == "name":
        return (
            "➕ <b>НОВЫЙ ТОВАР</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Шаг <b>1/4</b>\n\n"
            "Введите название товара.\n\n"
            "Например:\n"
            "<code>Premium Pack</code>"
        )

    if step == "description":
        return (
            "➕ <b>НОВЫЙ ТОВАР</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Шаг <b>2/4</b>\n\n"
            "Введите описание товара."
        )

    if step == "price":
        return (
            "➕ <b>НОВЫЙ ТОВАР</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Шаг <b>3/4</b>\n\n"
            "Введите цену в рублях.\n\n"
            "Например:\n"
            "<code>499</code>"
        )

    return (
        "➕ <b>НОВЫЙ ТОВАР</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Шаг <b>4/4</b>\n\n"
        "Отправьте продаваемый файл "
        "как <b>документ Telegram</b>."
    )


def admin_cancel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn(
                    "🔴  Отменить",
                    callback_data="admin:add_cancel",
                    style="danger",
                )
            ]
        ]
    )


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
        "step": "name",
    }

    await safe_answer(callback)

    await callback.message.edit_text(
        admin_add_text("name"),
        reply_markup=admin_cancel_keyboard(),
    )


@dp.callback_query(
    F.data == "admin:add_cancel"
)
async def admin_add_cancel(
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

    admin_states.pop(
        callback.from_user.id,
        None,
    )

    await safe_answer(callback)

    await callback.message.edit_text(
        admin_text(),
        reply_markup=admin_menu(),
    )


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
                "❌ Введите название текстом."
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
            admin_add_text(
                "description"
            ),
            reply_markup=admin_cancel_keyboard(),
        )

        return

    # --------------------------------------------------------
    # DESCRIPTION
    # --------------------------------------------------------

    if step == "description":

        if not message.text:
            await message.answer(
                "❌ Введите описание текстом."
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
            admin_add_text("price"),
            reply_markup=admin_cancel_keyboard(),
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
                message.text
                .replace(",", ".")
                .strip()
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
            admin_add_text("file"),
            reply_markup=admin_cancel_keyboard(),
        )

        return

    # --------------------------------------------------------
    # FILE
    # --------------------------------------------------------

    if step == "file":

        if not message.document:
            await message.answer(
                "❌ Отправьте файл именно "
                "как документ Telegram."
            )
            return

        document = message.document

        original_filename = (
            document.file_name
            or "digital_file.bin"
        )

        safe_filename = (
            secrets.token_hex(8)
            + "_"
            + original_filename
        )

        destination = (
            FILES_DIR / safe_filename
        )

        try:
            tg_file = await bot.get_file(
                document.file_id
            )

            await bot.download_file(
                tg_file.file_path,
                destination=destination,
            )

        except Exception as exc:
            logger.exception(
                "File download error: %s",
                exc,
            )

            await message.answer(
                "❌ Не удалось сохранить файл.\n\n"
                "Попробуйте ещё раз."
            )

            return

        product_id = add_product(
            name=state["name"],
            description=state["description"],
            price=state["price"],
            file_path=str(destination),
            original_filename=original_filename,
        )

        product_name = state["name"]
        price = state["price"]

        admin_states.pop(
            user_id,
            None,
        )

        await message.answer(
            "✅ <b>ТОВАР ДОБАВЛЕН</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 ID: <code>#{product_id}</code>\n"
            f"📄 <b>{product_name}</b>\n"
            f"💰 <b>{price:.2f} ₽</b>\n"
            f"📁 <code>{original_filename}</code>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Товар уже доступен в каталоге.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        btn(
                            "📦  Открыть товары",
                            callback_data=(
                                "admin:products"
                            ),
                            style="primary",
                        )
                    ],
                    [
                        btn(
                            "➕  Добавить ещё",
                            callback_data=(
                                "admin:add"
                            ),
                            style="success",
                        )
                    ],
                ]
            ),
        )

        return


# ============================================================
# ADMIN DELETE CONFIRMATION
# ============================================================

@dp.callback_query(
    F.data.startswith("admin:delete:")
)
async def admin_delete_confirm(
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
        return

    product = get_product(product_id)

    if not product:
        await callback.answer(
            "Товар уже удалён.",
            show_alert=True,
        )

        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn(
                    "🔴  ДА, УДАЛИТЬ",
                    callback_data=(
                        f"admin:delete_confirm:"
                        f"{product_id}"
                    ),
                    style="danger",
                ),
            ],
            [
                btn(
                    "🔙  Отмена",
                    callback_data="admin:products",
                    style="primary",
                ),
            ],
        ]
    )

    await safe_answer(callback)

    await callback.message.edit_text(
        "🗑 <b>УДАЛЕНИЕ ТОВАРА</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📄 <b>{product['name']}</b>\n"
        f"💰 {product['price']:.2f} ₽\n\n"
        "Вы действительно хотите удалить "
        "этот товар?\n\n"
        "⚠️ Файл товара также будет удалён "
        "с сервера.\n\n"
        "━━━━━━━━━━━━━━━━━━",
        reply_markup=keyboard,
    )


# ============================================================
# ADMIN DELETE
# ============================================================

@dp.callback_query(
    F.data.startswith(
        "admin:delete_confirm:"
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
        return

    product = get_product(product_id)

    if not product:
        await callback.answer(
            "Товар уже удалён.",
            show_alert=True,
        )

        return

    await safe_answer(callback)

    await callback.message.edit_text(
        "🗑 <b>УДАЛЕНИЕ</b>\n\n"
        "⏳ Удаляю товар...",
    )

    await asyncio.sleep(0.25)

    file_path = Path(
        product["file_path"]
    )

    delete_product(product_id)

    try:
        if file_path.exists():
            file_path.unlink()
    except Exception:
        logger.exception(
            "Could not delete product file"
        )

    await callback.message.edit_text(
        "✅ <b>ТОВАР УДАЛЁН</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📄 <b>{product['name']}</b>\n\n"
        "Товар успешно удалён "
        "из каталога.\n\n"
        "━━━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    btn(
                        "📦  К товарам",
                        callback_data=(
                            "admin:products"
                        ),
                        style="primary",
                    )
                ],
                [
                    btn(
                        "⚙️  Админ-панель",
                        callback_data="admin",
                        style="primary",
                    )
                ],
            ]
        ),
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
            "⚠️ <b>ОПЛАТА ПОДТВЕРЖДЕНА</b>\n\n"
            "Платёж успешно получен, "
            "но файл товара временно недоступен.\n\n"
            "Администратор уже уведомлён.",
        )

        logger.error(
            "Product file missing: %s",
            file_path,
        )

        return False

    await bot.send_message(
        user_id,
        "🎉 <b>ОПЛАТА ПОЛУЧЕНА!</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 <b>{product['name']}</b>\n\n"
        "⚡ Ваш файл уже готов.\n"
        "Отправляю его следующим сообщением.\n\n"
        "Спасибо за покупку! ❤️\n\n"
        "━━━━━━━━━━━━━━━━━━",
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
            "✅ Покупка успешно завершена."
        ),
    )

    return True


# ============================================================
# YOOMONEY SIGNATURE
# ============================================================

def verify_yoomoney_signature(
    data: dict,
):
    received_sign = data.get(
        "sha1_hash"
    ) or data.get(
        "sign"
    )

    if not received_sign:
        return False

    # --------------------------------------------------------
    # ЮMoney HTTP notifications.
    #
    # The exact signing scheme can depend
    # on the notification configuration.
    #
    # First try SHA256 HMAC of sorted
    # parameters without the signature.
    # --------------------------------------------------------

    params = {}

    for key, value in data.items():

        if key in (
            "sign",
            "sha1_hash",
        ):
            continue

        if isinstance(value, list):
            value = value[0]

        params[key] = str(value)

    prepared_parts = []

    for key, value in sorted(
        params.items(),
        key=lambda item: item[0],
    ):
        prepared_parts.append(
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

    prepared = "&".join(
        prepared_parts
    )

    expected_sha256 = hmac.new(
        YOOMONEY_SECRET.encode(
            "utf-8"
        ),
        prepared.encode(
            "utf-8"
        ),
        hashlib.sha256,
    ).hexdigest()

    if hmac.compare_digest(
        expected_sha256.lower(),
        str(received_sign).lower(),
    ):
        return True

    # --------------------------------------------------------
    # Fallback SHA1 HMAC.
    # --------------------------------------------------------

    expected_sha1 = hmac.new(
        YOOMONEY_SECRET.encode(
            "utf-8"
        ),
        prepared.encode(
            "utf-8"
        ),
        hashlib.sha1,
    ).hexdigest()

    return hmac.compare_digest(
        expected_sha1.lower(),
        str(received_sign).lower(),
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
            key: value
            for key, value in post.items()
        }

        logger.info(
            "YuMoney notification received: %s",
            data,
        )

        if not YOOMONEY_SECRET:
            logger.error(
                "YOOMONEY_SECRET is empty"
            )

            return web.Response(
                status=500,
                text="secret not configured",
            )

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

        label = data.get("label")

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
            amount_received = 0.0

        expected_amount = float(
            order["amount"]
        )

        # ----------------------------------------------------
        # Amount protection
        # ----------------------------------------------------

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
                "⚠️ <b>НЕПОЛНАЯ ОПЛАТА</b>\n\n"
                f"Получено: "
                f"<b>{amount_received:.2f} ₽</b>\n"
                f"Необходимо: "
                f"<b>{expected_amount:.2f} ₽</b>\n\n"
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
            paid_order["product_id"]
        )

        if not product:

            await bot.send_message(
                paid_order["user_id"],
                "⚠️ <b>ПЛАТЁЖ ПОДТВЕРЖДЁН</b>\n\n"
                "Платёж получен, но товар "
                "не найден.\n\n"
                "Администратор уже уведомлён.",
            )

            return web.Response(
                status=200,
                text="product missing",
            )

        # ----------------------------------------------------
        # Deliver file
        # ----------------------------------------------------

        try:
            await send_product_to_user(
                paid_order["user_id"],
                product,
            )

        except Exception as exc:

            logger.exception(
                "Delivery error: %s",
                exc,
            )

            await bot.send_message(
                paid_order["user_id"],
                "⚠️ <b>ПЛАТЁЖ ПОДТВЕРЖДЁН</b>\n\n"
                "Возникла ошибка при отправке "
                "файла.\n\n"
                "Администратор уведомлён.",
            )

        # ----------------------------------------------------
        # Admin notification
        # ----------------------------------------------------

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
                    f"<b>{amount_received:.2f} ₽</b>\n"
                    f"👤 User ID: "
                    f"<code>{paid_order['user_id']}</code>\n"
                    f"🔑 Label: "
                    f"<code>{label}</code>\n"
                    f"🆔 Operation: "
                    f"<code>{operation_id}</code>\n\n"
                    "━━━━━━━━━━━━━━━━━━",
                )

            except Exception:
                logger.exception(
                    "Could not notify admin"
                )

        return web.Response(
            status=200,
            text="ok",
        )

    except Exception as exc:

        logger.exception(
            "YuMoney webhook error: %s",
            exc,
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
            text="OK",
        )

    except Exception as exc:

        logger.exception(
            "Telegram webhook error: %s",
            exc,
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
        text="SEVAN Market is running.",
    )


# ============================================================
# ROOT
# ============================================================

async def root(
    request: web.Request,
):
    return web.Response(
        status=200,
        text=(
            "SEVAN Market\n"
            "Digital Store Bot\n"
            "OK"
        ),
    )


# ============================================================
# HTTP SERVER
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
# CONFIG VALIDATION
# ============================================================

def validate_config():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан."
        )

    if not YOOMONEY_WALLET:
        logger.warning(
            "YOOMONEY_WALLET не задан. "
            "Оплата ЮMoney работать не будет."
        )

    if not YOOMONEY_SECRET:
        logger.warning(
            "YOOMONEY_SECRET не задан. "
            "Webhook ЮMoney работать не будет."
        )

    if not WEBHOOK_BASE_URL:
        raise RuntimeError(
            "WEBHOOK_BASE_URL не задан."
        )

    if not ADMIN_IDS:
        raise RuntimeError(
            "ADMIN_IDS не задан."
        )


# ============================================================
# SET TELEGRAM WEBHOOK
# ============================================================

async def set_telegram_webhook():

    webhook_url = (
        WEBHOOK_BASE_URL
        + "/telegram/webhook"
    )

    await bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True,
    )

    logger.info(
        "Telegram webhook URL: %s",
        webhook_url,
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    validate_config()

    init_db()

    web_runner = (
        await start_web_server()
    )

    try:

        await set_telegram_webhook()

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
            "YuMoney webhook: %s/yoomoney",
            WEBHOOK_BASE_URL,
        )

        logger.info(
            "================================="
        )

        # Keep process alive.
        await asyncio.Event().wait()

    finally:

        try:
            await bot.delete_webhook(
                drop_pending_updates=False
            )
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
            "SEVAN Market stopped."
        )
