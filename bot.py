# ============================================================
# SEVAN MARKET
# Telegram Digital Store + ЮMoney
#
# Один файл: bot.py
#
# Для Render:
#
# Build Command:
# pip install -r requirements.txt
#
# Start Command:
# python bot.py
#
# requirements.txt:
# aiogram>=3.22,<4
# aiohttp>=3.10,<4
#
# ENV:
#
# BOT_TOKEN=токен_бота
# YOOMONEY_WALLET=номер_кошелька
# YOOMONEY_SECRET=секрет_ЮMoney
# WEBHOOK_BASE_URL=https://твой-домен.onrender.com
# ADMIN_IDS=8346538289
#
# Комиссия ЮMoney:
# 3%
#
# Товар 10 ₽:
# пользователь платит 10 ₽
# на кошелёк приходит 9.70 ₽
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

WEB_PORT = int(
    os.getenv(
        "PORT",
        "10000",
    )
)

SHOP_NAME = "SEVAN MARKET"

CURRENCY = "₽"

# Комиссия ЮMoney.
# 3% от 10 ₽ = 0.30 ₽
# На кошелёк приходит 9.70 ₽.
YOOMONEY_COMMISSION_RATE = 3.0

# Допустимая погрешность суммы.
# Используется из-за округления.
PAYMENT_TOLERANCE = 0.02

# Администраторы.
# Можно указать:
# ADMIN_IDS=123456789,987654321
#
admin_ids_raw = os.getenv(
    "ADMIN_IDS",
    "8346538289",
)

ADMIN_IDS = set()

for value in admin_ids_raw.split(","):
    value = value.strip()

    if value.isdigit():
        ADMIN_IDS.add(
            int(value)
        )


# ============================================================
# PATHS
# ============================================================

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

logger = logging.getLogger(
    "sevan-market"
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

    # Индекс для быстрой защиты от повторной операции.
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_orders_operation_id
        ON orders(operation_id)
        WHERE operation_id IS NOT NULL
        AND operation_id != ''
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


def get_product(
    product_id: int,
):

    connection = db()

    row = connection.execute(
        """
        SELECT *
        FROM products
        WHERE id=?
        """,
        (
            product_id,
        ),
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
        WHERE id=?
        """,
        (
            product_id,
        ),
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


def get_order_by_label(
    label: str,
):

    connection = db()

    row = connection.execute(
        """
        SELECT *
        FROM orders
        WHERE label=?
        """,
        (
            label,
        ),
    ).fetchone()

    connection.close()

    return row


def get_order_by_operation(
    operation_id: str,
):

    if not operation_id:
        return None

    connection = db()

    row = connection.execute(
        """
        SELECT *
        FROM orders
        WHERE operation_id=?
        LIMIT 1
        """,
        (
            operation_id,
        ),
    ).fetchone()

    connection.close()

    return row


def mark_order_paid(
    label: str,
    operation_id: str,
):

    connection = db()

    # Повторно проверяем заказ прямо перед изменением.
    row = connection.execute(
        """
        SELECT *
        FROM orders
        WHERE label=?
        AND status='pending'
        """,
        (
            label,
        ),
    ).fetchone()

    if not row:
        connection.close()
        return None

    # Дополнительная защита от повторного operation_id.
    if operation_id:

        existing = connection.execute(
            """
            SELECT id
            FROM orders
            WHERE operation_id=?
            LIMIT 1
            """,
            (
                operation_id,
            ),
        ).fetchone()

        if existing:
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
        AND status='pending'
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

        WHERE orders.user_id=?

        ORDER BY orders.id DESC
        """,
        (
            user_id,
        ),
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

    connection.close()

    return (
        users,
        products,
        orders,
        paid_orders,
        revenue,
    )


# ============================================================
# PAYMENT CALCULATIONS
# ============================================================

def calculate_yoomoney_received(
    amount: float,
) -> float:
    """
    Сумма, которая должна прийти
    на кошелёк после комиссии.

    10 ₽ -> 9.70 ₽
    """

    commission = (
        amount
        * YOOMONEY_COMMISSION_RATE
        / 100
    )

    received = (
        amount - commission
    )

    return round(
        received,
        2,
    )


def amounts_match(
    received: float,
    expected: float,
) -> bool:

    return (
        abs(
            round(received, 2)
            -
            round(expected, 2)
        )
        <= PAYMENT_TOLERANCE
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
# KEYBOARDS
# ============================================================

def main_menu():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛍  КАТАЛОГ",
                    callback_data="catalog",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📦  МОИ ПОКУПКИ",
                    callback_data="purchases",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💎  КАК ЭТО РАБОТАЕТ",
                    callback_data="how",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💬  ПОДДЕРЖКА",
                    callback_data="support",
                ),
            ],
        ]
    )


def home_button():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⌂  Главное меню",
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
                    text="💳  КУПИТЬ",
                    callback_data=f"buy:{product_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="‹  Назад к каталогу",
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
                    text="＋  Добавить товар",
                    callback_data="admin_add",
                )
            ],
            [
                InlineKeyboardButton(
                    text="▣  Товары",
                    callback_data="admin_products",
                ),
                InlineKeyboardButton(
                    text="◈  Статистика",
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
                        f"🗑  "
                        f"{product['name']}"
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
                text="‹  Панель администратора",
                callback_data="admin",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# HOME TEXT
# ============================================================

def home_text():

    return (
        f"✦ <b>{SHOP_NAME}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        "Цифровые товары с "
        "<b>мгновенной выдачей</b>.\n\n"

        "▸ 📁 Файлы\n"
        "▸ ⚡ Автоматическая доставка\n"
        "▸ 💳 Оплата через ЮMoney\n"
        "▸ 🔐 Уникальный заказ\n\n"

        "<i>Выберите нужный раздел ниже.</i>"
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
            "Пока здесь нет товаров.\n\n"
            "<i>Загляните позже.</i>",
            reply_markup=home_button(),
        )

        await callback.answer()
        return

    buttons = []

    for product in products:

        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"📄  {product['name']}"
                        f"  •  "
                        f"{product['price']:.2f} ₽"
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
                text="⌂  Главное меню",
                callback_data="home",
            )
        ]
    )

    await callback.message.edit_text(
        "🛍 <b>КАТАЛОГ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите цифровой товар:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
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
    except (
        ValueError,
        IndexError,
    ):

        await callback.answer(
            "Товар не найден.",
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
        "📄 <b>"
        f"{product['name']}"
        "</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"{product['description']}\n\n"

        "💰 Цена: "
        f"<b>{product['price']:.2f} ₽</b>\n"

        "⚡ Получение: "
        "<b>сразу после оплаты</b>\n\n"

        "<i>Нажмите кнопку ниже, "
        "чтобы оформить заказ.</i>"
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

    payment_url = (
        "https://yoomoney.ru/quickpay/confirm?"
        + urllib.parse.urlencode(
            {
                "receiver":
                    YOOMONEY_WALLET,

                "quickpay-form":
                    "button",

                "sum":
                    f"{float(product['price']):.2f}",

                "paymentType":
                    "AC",

                "label":
                    label,
            }
        )
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳  ОПЛАТИТЬ",
                    url=payment_url,
                )
            ],
            [
                InlineKeyboardButton(
                    text="✓  Я ОПЛАТИЛ",
                    callback_data=(
                        f"check:{label}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="‹  Отмена",
                    callback_data="catalog",
                )
            ],
        ]
    )

    text = (
        "💳 <b>ОФОРМЛЕНИЕ ЗАКАЗА</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Товар: "
        f"<b>{product['name']}</b>\n"

        f"💰 К оплате: "
        f"<b>{product['price']:.2f} ₽</b>\n"

        f"🧾 Заказ: "
        f"<code>#{order_id}</code>\n\n"

        "1. Нажмите «ОПЛАТИТЬ».\n"
        "2. Завершите оплату на странице ЮMoney.\n"
        "3. Вернитесь в бот и нажмите "
        "«Я ОПЛАТИЛ».\n\n"

        "⚡ После подтверждения файл "
        "будет отправлен автоматически."
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
    )

    await callback.answer()


# ============================================================
# MANUAL PAYMENT CHECK
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
            "Этот заказ принадлежит другому пользователю.",
            show_alert=True,
        )

        return

    if order["status"] == "paid":

        await callback.answer(
            "✓ Оплата уже подтверждена.",
            show_alert=True,
        )

        return

    await callback.answer(
        "⏳ Проверяем платёж...",
        show_alert=True,
    )

    # Важно:
    # ЮMoney само отправляет webhook.
    # Кнопка здесь не делает фиктивное
    # подтверждение платежа.
    #
    # Если webhook уже пришёл —
    # заказ будет paid.
    #
    # Если webhook ещё не пришёл —
    # ждём его.


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
            "У вас пока нет заказов.",
            reply_markup=home_button(),
        )

        await callback.answer()
        return

    text = (
        "📦 <b>МОИ ПОКУПКИ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    for row in rows[:15]:

        if row["status"] == "paid":
            status = "✓ Оплачен"
        else:
            status = "◷ Ожидает оплаты"

        text += (
            f"▸ <b>"
            f"{row['product_name'] or 'Товар'}"
            f"</b>\n"
            f"  {row['amount']:.2f} ₽"
            f"  •  {status}\n"
            f"  Заказ: "
            f"<code>{row['label']}</code>\n\n"
        )

    await callback.message.edit_text(
        text,
        reply_markup=home_button(),
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

        "<b>01</b>  🛍 Выберите товар\n"
        "Откройте каталог и выберите "
        "нужный файл.\n\n"

        "<b>02</b>  💳 Оплатите\n"
        "Бот создаст уникальный заказ "
        "и откроет страницу ЮMoney.\n\n"

        "<b>03</b>  🔐 Подтверждение\n"
        "ЮMoney отправляет уведомление "
        "о платеже.\n\n"

        "<b>04</b>  📁 Получите файл\n"
        "После подтверждения оплаты "
        "бот автоматически отправит "
        "купленный файл.\n\n"

        "⚡ Всё происходит автоматически."
    )

    await callback.message.edit_text(
        text,
        reply_markup=home_button(),
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

        "Возникла проблема с оплатой "
        "или получением товара?\n\n"

        "Сначала убедитесь, что платёж "
        "был успешно завершён.\n\n"

        "Если файл не пришёл после "
        "подтверждения оплаты — "
        "обратитесь к администратору."
    )

    await callback.message.edit_text(
        text,
        reply_markup=home_button(),
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

    await callback.message.edit_text(
        home_text(),
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
            "⛔ Доступ запрещён."
        )

        return

    await message.answer(
        "⚙️ <b>ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите действие:",
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

        f"✓ Оплачено: "
        f"<b>{paid_orders}</b>\n"

        f"💰 Продажи: "
        f"<b>{revenue:.2f} ₽</b>\n"
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
                f"#{product['id']}  "
                f"<b>{product['name']}</b>\n"
                f"💰 {product['price']:.2f} ₽\n\n"
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
        "＋ <b>ДОБАВЛЕНИЕ ТОВАРА</b>\n"
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

    if not is_admin(
        user_id
    ):
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

        if not state["name"]:

            await message.answer(
                "❌ Название не может быть пустым."
            )

            return

        state["step"] = (
            "description"
        )

        await message.answer(
            "📝 <b>Описание</b>\n\n"
            "Отправьте описание товара:"
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

        state["description"] = (
            message.text.strip()
        )

        state["step"] = "price"

        await message.answer(
            "💰 <b>Цена</b>\n\n"
            "Введите цену в рублях.\n\n"
            "Например:\n"
            "<code>10</code>"
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

            price = round(
                price,
                2,
            )

        except ValueError:

            await message.answer(
                "❌ Неверная цена.\n\n"
                "Например: "
                "<code>10</code>"
            )

            return

        state["price"] = price
        state["step"] = "file"

        await message.answer(
            "📁 <b>ФАЙЛ</b>\n\n"
            "Теперь отправьте файл "
            "именно как документ Telegram.\n\n"
            "<i>Например PDF, ZIP, TXT, DOCX и т.д.</i>"
        )

        return

    # --------------------------------------------------------
    # FILE
    # --------------------------------------------------------

    if step == "file":

        if not message.document:

            await message.answer(
                "❌ Нужно отправить файл "
                "именно как документ.\n\n"
                "Не отправляйте его как фото."
            )

            return

        document = (
            message.document
        )

        try:

            telegram_file = (
                await bot.get_file(
                    document.file_id
                )
            )

            original_name = (
                document.file_name
                or "file.bin"
            )

            # Убираем опасные символы
            # из имени.
            safe_original_name = (
                Path(
                    original_name
                ).name
            )

            safe_name = (
                f"{secrets.token_hex(8)}_"
                f"{safe_original_name}"
            )

            destination = (
                FILES_DIR
                / safe_name
            )

            await bot.download_file(
                telegram_file.file_path,
                destination=destination,
            )

        except Exception as error:

            logger.exception(
                "File download error: %s",
                error,
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
            file_path=str(
                destination
            ),
        )

        product_name = state["name"]
        product_price = state["price"]

        del admin_states[
            user_id
        ]

        await message.answer(
            "✓ <b>ТОВАР ДОБАВЛЕН</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"

            f"🆔 ID: "
            f"<code>{product_id}</code>\n"

            f"📄 Название: "
            f"<b>{product_name}</b>\n"

            f"💰 Цена: "
            f"<b>{product_price:.2f} ₽</b>\n"

            f"📁 Файл: "
            f"<code>{original_name}</code>\n\n"

            "Товар уже доступен в каталоге."
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
            "Не удалось удалить файл товара."
        )

    await callback.message.edit_text(
        "✓ <b>ТОВАР УДАЛЁН</b>\n\n"
        "Изменения сохранены.",
        reply_markup=admin_products_keyboard(),
    )

    await callback.answer()


# ============================================================
# YOOMONEY SIGNATURE
# ============================================================

def verify_yoomoney_signature(
    data: dict,
) -> bool:
    """
    Проверяет HMAC-SHA256 подпись
    уведомления ЮMoney.
    """

    received_sign = data.get(
        "sha1_hash"
    )

    # В некоторых интеграциях
    # может использоваться sign.
    if not received_sign:
        received_sign = data.get(
            "sign"
        )

    if not received_sign:
        return False

    params = {}

    for key, value in data.items():

        if key in (
            "sign",
            "sha1_hash",
        ):
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

    prepared_parts = []

    for key, value in sorted_items:

        encoded_key = (
            urllib.parse.quote(
                str(key),
                safe="-_.~",
            )
        )

        encoded_value = (
            urllib.parse.quote(
                str(value),
                safe="-_.~",
            )
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
        str(
            received_sign
        ).lower(),
    )


# ============================================================
# PAYMENT VALIDATION
# ============================================================

def validate_payment(
    data: dict,
    order,
):
    """
    Проверка платежа.

    Например:

    Цена товара:
        10.00 ₽

    Комиссия:
        3%

    Ожидаемое зачисление:
        9.70 ₽
    """

    # --------------------------------------------------------
    # LABEL
    # --------------------------------------------------------

    received_label = str(
        data.get(
            "label",
            "",
        )
    ).strip()

    expected_label = str(
        order["label"]
    ).strip()

    if not received_label:

        return (
            False,
            "У платежа отсутствует label.",
        )

    if (
        received_label
        != expected_label
    ):

        return (
            False,
            "Label не совпадает с заказом.",
        )

    # --------------------------------------------------------
    # OPERATION ID
    # --------------------------------------------------------

    operation_id = str(
        data.get(
            "operation_id",
            "",
        )
    ).strip()

    if not operation_id:

        return (
            False,
            "Отсутствует operation_id.",
        )

    # --------------------------------------------------------
    # AMOUNT
    # --------------------------------------------------------

    try:

        amount_received = float(
            str(
                data.get(
                    "amount",
                    "0",
                )
            ).replace(
                ",",
                ".",
            )
        )

    except (
        ValueError,
        TypeError,
    ):

        return (
            False,
            "Некорректная сумма.",
        )

    if amount_received <= 0:

        return (
            False,
            "Сумма равна нулю.",
        )

    # --------------------------------------------------------
    # EXPECTED
    # --------------------------------------------------------

    order_amount = float(
        order["amount"]
    )

    expected_received = (
        calculate_yoomoney_received(
            order_amount
        )
    )

    # --------------------------------------------------------
    # COMPARE
    # --------------------------------------------------------

    if not amounts_match(
        amount_received,
        expected_received,
    ):

        return (
            False,
            (
                "Неверная сумма. "
                f"Получено "
                f"{amount_received:.2f} ₽, "
                f"ожидалось "
                f"{expected_received:.2f} ₽."
            ),
        )

    return (
        True,
        "OK",
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
            (
                "⚠️ <b>Оплата подтверждена.</b>\n\n"
                "Но файл товара сейчас "
                "недоступен.\n\n"
                "Администратор уже уведомлён."
            ),
        )

        logger.error(
            "File not found: %s",
            file_path,
        )

        return False

    await bot.send_message(
        user_id,
        (
            "🎉 <b>ОПЛАТА ПОДТВЕРЖДЕНА</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"

            f"📦 <b>{product['name']}</b>\n\n"

            "📁 Ваш файл отправляется "
            "следующим сообщением.\n\n"

            "Спасибо за покупку ❤️"
        ),
    )

    await bot.send_document(
        user_id,
        FSInputFile(
            path=file_path,
            filename=file_path.name,
        ),
        caption=(
            f"📄 <b>{product['name']}</b>\n\n"
            "✓ Покупка успешно завершена."
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
                "Invalid YuMoney signature."
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

        label = str(
            data.get(
                "label",
                "",
            )
        ).strip()

        if not label:

            return web.Response(
                status=200,
                text="no label",
            )

        # ----------------------------------------------------
        # ORDER
        # ----------------------------------------------------

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
        # PAYMENT
        # ----------------------------------------------------

        valid, reason = (
            validate_payment(
                data,
                order,
            )
        )

        if not valid:

            logger.warning(
                "Payment rejected: %s",
                reason,
            )

            return web.Response(
                status=200,
                text="payment rejected",
            )

        # ----------------------------------------------------
        # OPERATION
        # ----------------------------------------------------

        operation_id = str(
            data.get(
                "operation_id",
                "",
            )
        ).strip()

        # ----------------------------------------------------
        # DUPLICATE OPERATION
        # ----------------------------------------------------

        existing_operation = (
            get_order_by_operation(
                operation_id
            )
        )

        if existing_operation:

            logger.warning(
                "Duplicate operation: %s",
                operation_id,
            )

            return web.Response(
                status=200,
                text="operation already processed",
            )

        # ----------------------------------------------------
        # MARK PAID
        # ----------------------------------------------------

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
                "Product not found: %s",
                paid_order["product_id"],
            )

            await bot.send_message(
                paid_order["user_id"],
                (
                    "⚠️ <b>Оплата подтверждена.</b>\n\n"
                    "Товар временно недоступен.\n"
                    "Администратор уже уведомлён."
                ),
            )

            return web.Response(
                status=200,
                text="product missing",
            )

        # ----------------------------------------------------
        # AMOUNT
        # ----------------------------------------------------

        try:

            amount_received = float(
                str(
                    data.get(
                        "amount",
                        "0",
                    )
                ).replace(
                    ",",
                    ".",
                )
            )

        except (
            ValueError,
            TypeError,
        ):

            amount_received = 0.0

        expected_payment = float(
            paid_order["amount"]
        )

        expected_received = (
            calculate_yoomoney_received(
                expected_payment
            )
        )

        # ----------------------------------------------------
        # SEND FILE
        # ----------------------------------------------------

        try:

            await send_product_to_user(
                paid_order["user_id"],
                product,
            )

        except Exception as error:

            logger.exception(
                "Product delivery error: %s",
                error,
            )

            try:

                await bot.send_message(
                    paid_order["user_id"],
                    (
                        "⚠️ <b>Оплата подтверждена.</b>\n\n"
                        "Произошла ошибка "
                        "при отправке файла.\n\n"
                        "Администратор уже уведомлён."
                    ),
                )

            except Exception:
                pass

        # ----------------------------------------------------
        # ADMIN NOTIFICATION
        # ----------------------------------------------------

        for admin_id in ADMIN_IDS:

            try:

                await bot.send_message(
                    admin_id,
                    (
                        "💰 <b>НОВАЯ ОПЛАТА</b>\n"
                        "━━━━━━━━━━━━━━━━━━\n\n"

                        f"🧾 Заказ: "
                        f"<code>#{paid_order['id']}</code>\n"

                        f"📦 Товар: "
                        f"<b>{product['name']}</b>\n\n"

                        f"💳 Цена: "
                        f"<b>{expected_payment:.2f} ₽</b>\n"

                        f"📉 Комиссия: "
                        f"<b>{YOOMONEY_COMMISSION_RATE:.2f}%</b>\n"

                        f"💰 Зачислено: "
                        f"<b>{amount_received:.2f} ₽</b>\n"

                        f"✓ Ожидалось: "
                        f"<b>{expected_received:.2f} ₽</b>\n\n"

                        f"👤 User ID: "
                        f"<code>{paid_order['user_id']}</code>\n"

                        f"🔑 Label: "
                        f"<code>{label}</code>\n"

                        f"🆔 Operation: "
                        f"<code>{operation_id}</code>\n\n"

                        "✅ <b>Платёж подтверждён.</b>"
                    ),
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

        data = await request.json()

        update = (
            __import__(
                "aiogram.types",
                fromlist=["Update"],
            ).Update
            .model_validate(data)
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
        text=(
            "SEVAN MARKET is running."
        ),
    )


async def root(
    request: web.Request,
):

    return web.Response(
        status=200,
        text=(
            "SEVAN MARKET BOT"
        ),
    )


# ============================================================
# VALIDATE CONFIG
# ============================================================

def validate_config():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN не задан."
        )

    if not YOOMONEY_WALLET:

        raise RuntimeError(
            "YOOMONEY_WALLET не задан."
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


# ============================================================
# WEB SERVER
# ============================================================

async def start_web_server():

    app = web.Application()

    # Telegram
    app.router.add_post(
        "/telegram/webhook",
        telegram_webhook,
    )

    # ЮMoney
    app.router.add_post(
        "/yoomoney",
        yoomoney_webhook,
    )

    # Health
    app.router.add_get(
        "/health",
        health,
    )

    # Root
    app.router.add_get(
        "/",
        root,
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
# MAIN
# ============================================================

async def main():

    validate_config()

    init_db()

    runner = await start_web_server()

    telegram_webhook_url = (
        f"{WEBHOOK_BASE_URL}"
        "/telegram/webhook"
    )

    yoomoney_webhook_url = (
        f"{WEBHOOK_BASE_URL}"
        "/yoomoney"
    )

    try:

        # ----------------------------------------------------
        # TELEGRAM WEBHOOK
        # ----------------------------------------------------

        await bot.set_webhook(
            url=telegram_webhook_url,
            drop_pending_updates=True,
        )

        logger.info(
            "Telegram webhook URL: %s",
            telegram_webhook_url,
        )

        # ----------------------------------------------------
        # START INFO
        # ----------------------------------------------------

        logger.info(
            "================================="
        )

        logger.info(
            "%s started successfully",
            SHOP_NAME,
        )

        logger.info(
            "Telegram webhook: %s",
            telegram_webhook_url,
        )

        logger.info(
            "YuMoney webhook: %s",
            yoomoney_webhook_url,
        )

        logger.info(
            "YuMoney commission rate: %.2f%%",
            YOOMONEY_COMMISSION_RATE,
        )

        logger.info(
            "For 10.00 RUB expected wallet amount: %.2f RUB",
            calculate_yoomoney_received(
                10.00
            ),
        )

        logger.info(
            "================================="
        )

        # ----------------------------------------------------
        # KEEP ALIVE
        # ----------------------------------------------------

        await asyncio.Event().wait()

    finally:

        try:

            await bot.delete_webhook()

        except Exception:
            pass

        await runner.cleanup()

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
