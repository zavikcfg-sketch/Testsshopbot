# ============================================================
# DIGITAL MARKET — Telegram Digital Store
# Python 3.12+
#
# Telegram Bot API 10.x
# aiogram 3.x
# aiohttp
#
# Возможности:
#   🛍 Красивый каталог
#   💳 ЮMoney
#   📁 Автоматическая выдача файлов
#   📦 История покупок
#   👤 Пользовательская статистика
#   ⚙️ Админ-панель
#   ➕ Добавление товаров
#   🗑 Удаление товаров
#   📊 Статистика
#   🔔 Уведомления админу
#   🎨 Цветные кнопки Telegram API:
#      primary = синяя
#      success = зелёная
#      danger  = красная
#   🌐 Render webhook
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

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

YOOMONEY_WALLET = os.getenv(
    "YOOMONEY_WALLET",
    "",
)

YOOMONEY_SECRET = os.getenv(
    "YOOMONEY_SECRET",
    "",
)

WEBHOOK_BASE_URL = os.getenv(
    "WEBHOOK_BASE_URL",
    "",
).rstrip("/")

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv(
        "ADMIN_IDS",
        "8346538289",
    ).split(",")
    if x.strip().isdigit()
}

SHOP_NAME = os.getenv(
    "SHOP_NAME",
    "Digital Market",
)

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

TELEGRAM_WEBHOOK_PATH = "/telegram/webhook"
YOOMONEY_WEBHOOK_PATH = "/yoomoney"

TELEGRAM_WEBHOOK_URL = (
    f"{WEBHOOK_BASE_URL}"
    f"{TELEGRAM_WEBHOOK_PATH}"
)

YOOMONEY_WEBHOOK_URL = (
    f"{WEBHOOK_BASE_URL}"
    f"{YOOMONEY_WEBHOOK_PATH}"
)

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

logger = logging.getLogger(
    "digital-market"
)

# ============================================================
# TIME
# ============================================================


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


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
            paid_at TEXT,
            delivery_sent INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    # Для существующей базы добавляем delivery_sent,
    # если база была создана старой версией.
    try:
        connection.execute(
            """
            ALTER TABLE orders
            ADD COLUMN delivery_sent INTEGER
            NOT NULL DEFAULT 0
            """
        )
    except sqlite3.OperationalError:
        pass

    connection.commit()
    connection.close()


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


def get_product(
    product_id: int,
):

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


def delete_product(
    product_id: int,
):

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
            created_at,
            delivery_sent
        )
        VALUES (?, ?, ?, ?, ?, 'pending', ?, 0)
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


def get_order_by_label(
    label: str,
):

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
        AND status = 'pending'
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


def mark_delivery_sent(
    label: str,
):

    connection = db()

    connection.execute(
        """
        UPDATE orders
        SET delivery_sent = 1
        WHERE label = ?
        """,
        (label,),
    )

    connection.commit()
    connection.close()


def get_user_orders(
    user_id: int,
):

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
# TELEGRAM BOT
# ============================================================


bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
    ),
)

dp = Dispatcher()


# ============================================================
# BUTTON HELPERS
#
# Telegram Bot API:
#
# primary = blue
# success = green
# danger  = red
#
# ============================================================


def button(
    text: str,
    callback_data: str | None = None,
    url: str | None = None,
    style: str | None = None,
):

    kwargs = {
        "text": text,
    }

    if callback_data is not None:
        kwargs["callback_data"] = (
            callback_data
        )

    if url is not None:
        kwargs["url"] = url

    if style is not None:
        kwargs["style"] = style

    return InlineKeyboardButton(
        **kwargs
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
                    "primary",
                ),
            ],
            [
                button(
                    "📦 Мои покупки",
                    "purchases",
                    "success",
                ),
                button(
                    "💎 Как это работает",
                    "how",
                ),
            ],
            [
                button(
                    "💬 Поддержка",
                    "support",
                ),
            ],
        ]
    )


# ============================================================
# CATALOG KEYBOARD
# ============================================================


def catalog_keyboard():

    rows = []

    for product in get_products():

        rows.append(
            [
                button(
                    (
                        f"📄 {product['name']} "
                        f"• "
                        f"{product['price']:.2f} ₽"
                    ),
                    f"product:{product['id']}",
                    "primary",
                )
            ]
        )

    rows.append(
        [
            button(
                "🏠 Главное меню",
                "home",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
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
                button(
                    "💳 Купить сейчас",
                    f"buy:{product_id}",
                    "success",
                )
            ],
            [
                button(
                    "◀️ Назад к каталогу",
                    "catalog",
                )
            ],
        ]
    )


# ============================================================
# PAYMENT KEYBOARD
# ============================================================


def payment_keyboard(
    payment_url: str,
    label: str,
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
                    "🔄 Я оплатил — проверить",
                    f"check:{label}",
                    "primary",
                )
            ],
            [
                button(
                    "❌ Отменить заказ",
                    "catalog",
                    "danger",
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
                    "admin_add",
                    "success",
                )
            ],
            [
                button(
                    "📦 Товары",
                    "admin_products",
                    "primary",
                ),
                button(
                    "📊 Статистика",
                    "admin_stats",
                ),
            ],
            [
                button(
                    "🏠 Магазин",
                    "home",
                )
            ],
        ]
    )


# ============================================================
# ADMIN PRODUCTS
# ============================================================


def admin_products_keyboard():

    rows = []

    for product in get_products():

        rows.append(
            [
                button(
                    (
                        f"🗑 "
                        f"{product['name']}"
                    ),
                    f"delete:{product['id']}",
                    "danger",
                )
            ]
        )

    rows.append(
        [
            button(
                "◀️ Назад",
                "admin",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# TEXTS
# ============================================================


def home_text():

    return (
        f"✨ <b>{SHOP_NAME}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Добро пожаловать в магазин "
        "цифровых товаров.\n\n"
        "📁 <b>Файлы</b> — получаете прямо "
        "в Telegram\n"
        "⚡ <b>Моментальная выдача</b> — "
        "без ожидания\n"
        "💳 <b>ЮMoney</b> — удобная оплата\n"
        "🔐 <b>Безопасные заказы</b> — "
        "уникальная метка каждого платежа\n\n"
        "👇 <b>Выберите нужный раздел:</b>"
    )


def catalog_text():

    return (
        "🛍 <b>КАТАЛОГ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите цифровой товар "
        "из списка ниже.\n\n"
        "⚡ После оплаты файл будет "
        "отправлен автоматически."
    )


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
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

    await message.answer(
        home_text(),
        reply_markup=main_menu(),
    )


# ============================================================
# HOME
# ============================================================


@dp.callback_query(
    F.data == "home"
)
async def home(
    callback: CallbackQuery,
):

    await callback.message.edit_text(
        home_text(),
        reply_markup=main_menu(),
    )

    await callback.answer()


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

        await callback.message.edit_text(
            "🛍 <b>КАТАЛОГ</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "😔 Сейчас товаров нет.\n\n"
            "Загляните позже.",
            reply_markup=main_menu(),
        )

        await callback.answer()

        return

    await callback.message.edit_text(
        catalog_text(),
        reply_markup=catalog_keyboard(),
    )

    await callback.answer()


# ============================================================
# PRODUCT
# ============================================================


@dp.callback_query(
    F.data.startswith("product:")
)
async def product_view(
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
        "📦 <b>ТОВАР</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📄 <b>{product['name']}</b>\n\n"
        f"{product['description']}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💰 Цена: "
        f"<b>{product['price']:.2f} ₽</b>\n"
        "⚡ Выдача: <b>мгновенно</b>\n"
        "📁 Формат: <b>цифровой файл</b>\n\n"
        "Нажмите кнопку ниже, чтобы "
        "оформить покупку."
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
            callback.data.split(":")[1]
        )
    except Exception:
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
        amount=float(product["price"]),
    )

    payment_url = (
        "https://yoomoney.ru/quickpay/confirm?"
        + urllib.parse.urlencode(
            {
                "receiver":
                    YOOMONEY_WALLET,
                "quickpay-form":
                    "button",
                "sum":
                    f"{product['price']:.2f}",
                "paymentType":
                    "AC",
                "label":
                    label,
            }
        )
    )

    text = (
        "🧾 <b>ОФОРМЛЕНИЕ ЗАКАЗА</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 Товар:\n"
        f"<b>{product['name']}</b>\n\n"
        f"💰 К оплате:\n"
        f"<b>{product['price']:.2f} ₽</b>\n\n"
        f"🧾 Номер заказа:\n"
        f"<code>#{order_id}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ Нажмите "
        "«ОПЛАТИТЬ ЮMONEY».\n"
        "2️⃣ Завершите оплату.\n"
        "3️⃣ Вернитесь сюда и нажмите "
        "«Я оплатил — проверить».\n\n"
        "⚡ После подтверждения платежа "
        "файл будет отправлен автоматически."
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
# MANUAL CHECK
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

    if (
        order["user_id"]
        != callback.from_user.id
    ):

        await callback.answer(
            "Это не ваш заказ.",
            show_alert=True,
        )

        return

    if order["status"] == "paid":

        await callback.answer(
            "✅ Заказ уже оплачен.",
            show_alert=True,
        )

        return

    await callback.answer(
        "⏳ Оплата ещё не подтверждена.\n"
        "Если вы только что оплатили, "
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

    rows = get_user_orders(
        callback.from_user.id
    )

    if not rows:

        await callback.message.edit_text(
            "📦 <b>МОИ ПОКУПКИ</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "У вас пока нет заказов.\n\n"
            "🛍 Загляните в каталог "
            "и выберите товар.",
            reply_markup=main_menu(),
        )

        await callback.answer()

        return

    text = (
        "📦 <b>МОИ ПОКУПКИ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    for row in rows[:15]:

        if row["status"] == "paid":

            status = "🟢 Оплачен"

        else:

            status = "🟡 Ожидает оплаты"

        text += (
            f"📄 <b>"
            f"{row['product_name'] or 'Товар'}"
            f"</b>\n"
            f"💰 "
            f"{row['amount']:.2f} ₽\n"
            f"{status}\n"
            f"🧾 "
            f"<code>{row['label']}</code>\n\n"
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
        "💎 <b>КАК ЭТО РАБОТАЕТ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🛍 <b>1. Выбираете товар</b>\n"
        "Открываете каталог и выбираете "
        "нужный файл.\n\n"
        "💳 <b>2. Оплачиваете</b>\n"
        "Бот создаёт уникальный заказ "
        "и отправляет вас на страницу "
        "оплаты ЮMoney.\n\n"
        "🔐 <b>3. ЮMoney подтверждает платёж</b>\n"
        "Бот получает HTTP-уведомление "
        "и проверяет его подпись.\n\n"
        "📁 <b>4. Получаете файл</b>\n"
        "После успешного подтверждения "
        "файл автоматически отправляется "
        "в этот чат.\n\n"
        "⚡ Всё максимально быстро."
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(
                    "🛍 Открыть каталог",
                    "catalog",
                    "primary",
                )
            ],
            [
                button(
                    "🏠 Главное меню",
                    "home",
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
        "💬 <b>ПОДДЕРЖКА</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Если у вас возникла проблема "
        "с оплатой или получением товара, "
        "обратитесь к администратору.\n\n"
        "🧾 Подготовьте номер заказа — "
        "так мы быстрее найдём ваш платёж.\n\n"
        "Спасибо за покупку ❤️"
    )

    await callback.message.edit_text(
        text,
        reply_markup=main_menu(),
    )

    await callback.answer()


# ============================================================
# ADMIN
# ============================================================


def is_admin(
    user_id: int,
):

    return user_id in ADMIN_IDS


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
        "⚙️ <b>ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Управление магазином:",
        reply_markup=admin_menu(),
    )


# ============================================================
# ADMIN CALLBACK
# ============================================================


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
        "⚙️ <b>ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Управление магазином:",
        reply_markup=admin_menu(),
    )

    await callback.answer()


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
    ) = statistics()

    text = (
        "📊 <b>СТАТИСТИКА</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Пользователей: "
        f"<b>{users}</b>\n"
        f"📦 Товаров: "
        f"<b>{products}</b>\n"
        f"🧾 Заказов: "
        f"<b>{orders}</b>\n"
        f"✅ Оплачено: "
        f"<b>{paid_orders}</b>\n"
        f"💰 Выручка: "
        f"<b>{revenue:.2f} ₽</b>"
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
            "📦 <b>ТОВАРЫ</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Товаров пока нет."
        )

    else:

        text = (
            "📦 <b>ТОВАРЫ</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
        )

        for product in products:

            text += (
                f"#{product['id']} "
                f"• <b>{product['name']}</b>\n"
                f"💰 "
                f"{product['price']:.2f} ₽\n\n"
            )

    await callback.message.edit_text(
        text,
        reply_markup=admin_products_keyboard(),
    )

    await callback.answer()


# ============================================================
# ADMIN ADD PRODUCT
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
        "➕ <b>ДОБАВЛЕНИЕ ТОВАРА</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Введите название товара:"
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

    user_id = (
        message.from_user.id
    )

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

        state["name"] = (
            message.text.strip()
        )

        state["step"] = (
            "description"
        )

        await message.answer(
            "📝 <b>Описание товара</b>\n\n"
            "Отправьте описание:"
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

        state["description"] = (
            message.text.strip()
        )

        state["step"] = "price"

        await message.answer(
            "💰 <b>Цена</b>\n\n"
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
                message.text
                .replace(",", ".")
                .strip()
            )

            if price <= 0:
                raise ValueError

        except ValueError:

            await message.answer(
                "❌ Неверная цена.\n\n"
                "Пример: "
                "<code>499</code>"
            )

            return

        state["price"] = price

        state["step"] = "file"

        await message.answer(
            "📁 <b>Файл товара</b>\n\n"
            "Теперь отправьте файл "
            "как документ Telegram."
        )

        return

    # --------------------------------------------------------
    # FILE
    # --------------------------------------------------------

    if step == "file":

        if not message.document:

            await message.answer(
                "❌ Нужно отправить именно "
                "файл как документ Telegram."
            )

            return

        document = (
            message.document
        )

        try:

            tg_file = (
                await bot.get_file(
                    document.file_id
                )
            )

            original_name = (
                document.file_name
                or "file.bin"
            )

            safe_name = (
                f"{secrets.token_hex(8)}_"
                f"{original_name}"
            )

            destination = (
                FILES_DIR
                / safe_name
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
            description=state[
                "description"
            ],
            price=state["price"],
            file_path=str(
                destination
            ),
        )

        product_name = state[
            "name"
        ]

        product_price = state[
            "price"
        ]

        del admin_states[user_id]

        await message.answer(
            "✅ <b>ТОВАР ДОБАВЛЕН</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 ID: "
            f"<code>{product_id}</code>\n"
            f"📄 <b>{product_name}</b>\n"
            f"💰 {product_price:.2f} ₽\n"
            f"📁 {document.file_name}\n\n"
            "Товар уже доступен в каталоге.",
            reply_markup=admin_menu(),
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
            callback.data.split(":")[1]
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
            "File deletion error."
        )

    await callback.message.edit_text(
        "🗑 <b>ТОВАР УДАЛЁН</b>\n\n"
        f"Товар "
        f"<b>{product['name']}</b> "
        f"удалён.",
        reply_markup=admin_products_keyboard(),
    )

    await callback.answer(
        "Удалено."
    )


# ============================================================
# YOOMONEY SIGNATURE
# ============================================================


def verify_yoomoney_signature(
    data: dict,
):

    received_sign = data.get(
        "sign"
    )

    if not received_sign:
        return False

    parameters = {}

    for key, value in data.items():

        if key == "sign":
            continue

        if isinstance(
            value,
            list,
        ):

            value = value[0]

        parameters[
            str(key)
        ] = str(value)

    # ЮMoney требует:
    #
    # 1. убрать sign
    # 2. отсортировать параметры
    # 3. URL encode
    # 4. соединить через &
    # 5. HMAC-SHA256 с секретом
    #
    sorted_items = sorted(
        parameters.items(),
        key=lambda item: item[0],
    )

    prepared = "&".join(
        (
            f"{urllib.parse.quote("
            f"str(key), "
            f"safe='-_.~')}"
            f"="
            f"{urllib.parse.quote("
            f"str(value), "
            f"safe='-_.~')}"
        )
        for key, value
        in sorted_items
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
            "Но файл товара временно "
            "недоступен.\n\n"
            "Администратор уведомлён.",
        )

        logger.error(
            "Product file missing: %s",
            file_path,
        )

        return False

    await bot.send_message(
        user_id,
        "🎉 <b>ОПЛАТА ПОДТВЕРЖДЕНА!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 Товар:\n"
        f"<b>{product['name']}</b>\n\n"
        "📁 Ваш файл отправляется "
        "следующим сообщением.\n\n"
        "Спасибо за покупку ❤️",
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
            "YooMoney notification: %s",
            data,
        )

        # ----------------------------------------------------
        # SIGNATURE
        # ----------------------------------------------------

        if not verify_yoomoney_signature(
            data
        ):

            logger.warning(
                "Invalid YooMoney signature."
            )

            return web.Response(
                status=403,
                text="invalid signature",
            )

        # ----------------------------------------------------
        # TYPE
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
        # LABEL
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # ALREADY PAID
        # ----------------------------------------------------

        if order["status"] == "paid":

            return web.Response(
                status=200,
                text="already paid",
            )

        # ----------------------------------------------------
        # AMOUNT
        # ----------------------------------------------------

        try:

            amount_received = float(
                data.get(
                    "amount",
                    "0",
                )
            )

        except (ValueError, TypeError):

            amount_received = 0.0

        expected_amount = float(
            order["amount"]
        )

        if (
            amount_received + 0.0001
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
                "⚠️ <b>ПЛАТЁЖ ПОЛУЧЕН</b>\n\n"
                "Но сумма не совпадает "
                "с заказом.\n\n"
                f"Получено: "
                f"<b>{amount_received:.2f} ₽</b>\n"
                f"Нужно: "
                f"<b>{expected_amount:.2f} ₽</b>\n\n"
                "Обратитесь в поддержку.",
            )

            return web.Response(
                status=200,
                text="amount mismatch",
            )

        # ----------------------------------------------------
        # MARK PAID
        # ----------------------------------------------------

        operation_id = str(
            data.get(
                "operation_id",
                "",
            )
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

        # ----------------------------------------------------
        # PRODUCT
        # ----------------------------------------------------

        product = get_product(
            paid_order["product_id"]
        )

        if not product:

            logger.error(
                "Product missing for order %s",
                paid_order["id"],
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

        # ----------------------------------------------------
        # DELIVERY
        # ----------------------------------------------------

        try:

            delivered = (
                await send_product_to_user(
                    paid_order["user_id"],
                    product,
                )
            )

            if delivered:

                mark_delivery_sent(
                    label
                )

        except Exception as error:

            logger.exception(
                "Delivery error: %s",
                error,
            )

            await bot.send_message(
                paid_order["user_id"],
                "⚠️ <b>Платёж подтверждён.</b>\n\n"
                "Возникла ошибка при отправке "
                "файла.\n\n"
                "Администратор уведомлён.",
            )

        # ----------------------------------------------------
        # ADMIN NOTIFICATION
        # ----------------------------------------------------

        for admin_id in ADMIN_IDS:

            try:

                await bot.send_message(
                    admin_id,
                    "💰 <b>НОВАЯ ПРОДАЖА!</b>\n"
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
                    f"<code>{operation_id}</code>",
                )

            except Exception:

                logger.exception(
                    "Admin notification error."
                )

        return web.Response(
            status=200,
            text="ok",
        )

    except Exception as error:

        logger.exception(
            "YooMoney webhook error: %s",
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
        text="Digital Market Bot is running.",
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
            "Digital Market Bot is running."
        ),
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
            text="OK",
        )

    except Exception as error:

        logger.exception(
            "Telegram webhook error: %s",
            error,
        )

        return web.Response(
            status=200,
            text="OK",
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
# VALIDATE CONFIG
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

    if errors:

        raise RuntimeError(
            "\n".join(errors)
        )


# ============================================================
# SETUP TELEGRAM
# ============================================================


async def setup_telegram():

    logger.info(
        "Setting Telegram webhook: %s",
        TELEGRAM_WEBHOOK_URL,
    )

    await bot.set_webhook(
        url=TELEGRAM_WEBHOOK_URL,
        drop_pending_updates=False,
        allowed_updates=[
            "message",
            "callback_query",
        ],
    )

    logger.info(
        "Telegram webhook URL: %s",
        TELEGRAM_WEBHOOK_URL,
    )


# ============================================================
# BOT COMMANDS
# ============================================================


async def setup_commands():

    from aiogram.types import (
        BotCommand,
    )

    commands = [
        BotCommand(
            command="start",
            description="🏠 Главное меню",
        ),
        BotCommand(
            command="admin",
            description="⚙️ Панель администратора",
        ),
    ]

    await bot.set_my_commands(
        commands
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

        await setup_commands()

        await setup_telegram()

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
            "YooMoney webhook: %s",
            YOOMONEY_WEBHOOK_URL,
        )

        logger.info(
            "================================="
        )

        # ВАЖНО:
        # Используем webhook, поэтому
        # polling здесь НЕ запускаем.
        #
        # Telegram будет отправлять
        # обновления на /telegram/webhook.

        await asyncio.Event().wait()

    finally:

        await web_runner.cleanup()

        await bot.delete_webhook(
            drop_pending_updates=False
        )

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
