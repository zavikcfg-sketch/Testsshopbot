import asyncio
import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import urllib.parse
from datetime import datetime
from typing import Optional

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

# ЮMoney
YOOMONEY_WALLET = os.getenv(
    "YOOMONEY_WALLET",
    "41001XXXXXXXXXXXX"
)

YOOMONEY_SECRET = os.getenv(
    "YOOMONEY_SECRET",
    ""
)

# URL Render.
# Например:
# https://my-digital-shop.onrender.com
WEBHOOK_BASE_URL = os.getenv(
    "WEBHOOK_BASE_URL",
    ""
).rstrip("/")

# Порт Render
WEB_PORT = int(
    os.getenv("PORT", "10000")
)

# Администраторы
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv(
        "ADMIN_IDS",
        "8346538289"
    ).split(",")
    if x.strip()
}

# Название
SHOP_NAME = "Digital Market"

CURRENCY = "₽"

# База
DB_FILE = "store.db"

# Telegram webhook
TELEGRAM_WEBHOOK_PATH = "/telegram/webhook"

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)

# ============================================================
# BOT
# ============================================================

if not BOT_TOKEN:
    logger.warning(
        "BOT_TOKEN не задан. Добавь его в Render Environment."
    )

bot = Bot(
    token=BOT_TOKEN or "000000:PLACEHOLDER",
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    ),
)

dp = Dispatcher()

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
            telegram_file_id TEXT NOT NULL,
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


# ============================================================
# USERS
# ============================================================


def save_user(
    user_id: int,
    username: Optional[str],
    first_name: Optional[str],
):
    conn = db()

    conn.execute(
        """
        INSERT INTO users(
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
            datetime.utcnow().isoformat(),
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
    telegram_file_id: str,
    original_filename: str,
):
    conn = db()

    cur = conn.execute(
        """
        INSERT INTO products(
            name,
            description,
            price,
            telegram_file_id,
            original_filename,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            description,
            price,
            telegram_file_id,
            original_filename,
            datetime.utcnow().isoformat(),
        ),
    )

    product_id = cur.lastrowid

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
    username: Optional[str],
    product_id: int,
    amount: float,
):
    label = (
        "TG-"
        + secrets.token_hex(10).upper()
    )

    conn = db()

    cur = conn.execute(
        """
        INSERT INTO orders(
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
            datetime.utcnow().isoformat(),
        ),
    )

    order_id = cur.lastrowid

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


def get_user_orders(user_id: int):
    conn = db()

    rows = conn.execute(
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

    conn.close()

    return rows


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
            datetime.utcnow().isoformat(),
            label,
        ),
    )

    conn.commit()
    conn.close()

    return row


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

    paid_orders = conn.execute(
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
        paid_orders,
        revenue,
    )


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
                    text="💎 Как это работает",
                    callback_data="how",
                ),
                InlineKeyboardButton(
                    text="💬 Поддержка",
                    callback_data="support",
                ),
            ],
        ]
    )


def product_keyboard(product_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Купить",
                    callback_data=f"buy:{product_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
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
                    text="➕ Добавить товар",
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


def admin_products_keyboard():
    rows = []

    products = get_products()

    for product in products:
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"🗑 {product['name']}"
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
                text="◀️ Админ-панель",
                callback_data="admin",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# PAYMENT URL
# ============================================================


def create_payment_url(
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
        f"✨ <b>{SHOP_NAME}</b>\n\n"
        "Добро пожаловать в магазин "
        "цифровых товаров.\n\n"
        "📁 Мгновенная выдача файлов\n"
        "💳 Оплата через ЮMoney\n"
        "🔐 Безопасная обработка заказов\n"
        "⚡ Получение товара после оплаты\n\n"
        "<i>Выберите нужный раздел:</i>"
    )

    await message.answer(
        text,
        reply_markup=main_menu(),
    )


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
            "🛍 <b>Каталог</b>\n\n"
            "Пока товаров нет.",
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
                        f"📄 {product['name']} — "
                        f"{product['price']:.2f} "
                        f"{CURRENCY}"
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
        "🛍 <b>Каталог товаров</b>\n\n"
        "Выберите товар:",
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

    text = (
        f"📄 <b>{product['name']}</b>\n\n"
        f"{product['description']}\n\n"
        f"💰 Цена: "
        f"<b>{product['price']:.2f} "
        f"{CURRENCY}</b>\n\n"
        "⚡ Выдача товара автоматически\n\n"
        "Нажмите «Купить» для оплаты."
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

    order_id, label = create_order(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        product_id=product_id,
        amount=float(product["price"]),
    )

    payment_url = create_payment_url(
        float(product["price"]),
        label,
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 ОПЛАТИТЬ ЮMONEY",
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
                    text="❌ Отмена",
                    callback_data="catalog",
                )
            ],
        ]
    )

    text = (
        "💳 <b>Оформление заказа</b>\n\n"
        f"📦 Товар: "
        f"<b>{product['name']}</b>\n"
        f"💰 Сумма: "
        f"<b>{product['price']:.2f} "
        f"{CURRENCY}</b>\n"
        f"🧾 Заказ: "
        f"<code>#{order_id}</code>\n\n"
        "1️⃣ Нажмите кнопку оплаты.\n"
        "2️⃣ Оплатите через ЮMoney.\n"
        "3️⃣ ЮMoney отправит уведомление.\n"
        "4️⃣ Бот автоматически отправит файл.\n\n"
        "⚠️ Не передавайте ссылку на оплату "
        "другим людям."
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
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
        1
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
            "Этот заказ принадлежит "
            "другому пользователю.",
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
        "⏳ Оплата пока не подтверждена.\n\n"
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
            "📦 <b>Мои покупки</b>\n\n"
            "У вас пока нет заказов.",
            reply_markup=main_menu(),
        )

        await callback.answer()
        return

    text = (
        "📦 <b>Мои покупки</b>\n\n"
    )

    for row in rows[:15]:
        if row["status"] == "paid":
            status = "✅ Оплачен"
        else:
            status = "⏳ Ожидает оплаты"

        text += (
            f"• <b>"
            f"{row['product_name'] or 'Товар'}"
            f"</b>\n"
            f"  💰 {row['amount']:.2f} "
            f"{CURRENCY}\n"
            f"  {status}\n"
            f"  🧾 <code>{row['label']}</code>\n\n"
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
        "💎 <b>Как это работает</b>\n\n"
        "🛍 <b>1. Выбираете товар</b>\n"
        "Открываете каталог и выбираете файл.\n\n"
        "💳 <b>2. Оплачиваете</b>\n"
        "Бот создаёт уникальный заказ "
        "и открывает страницу ЮMoney.\n\n"
        "🔐 <b>3. ЮMoney подтверждает оплату</b>\n"
        "Платёж связан с уникальной меткой заказа.\n\n"
        "📁 <b>4. Получаете файл</b>\n"
        "После подтверждения платежа бот "
        "автоматически отправляет файл.\n\n"
        "⚡ Всё происходит автоматически."
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛍 Каталог",
                    callback_data="catalog",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Главное меню",
                    callback_data="home",
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
        "💬 <b>Поддержка</b>\n\n"
        "Если возникла проблема с оплатой "
        "или получением товара, обратитесь "
        "к администратору магазина.\n\n"
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
        "Цифровые товары "
        "с автоматической выдачей.\n\n"
        "📁 Файлы\n"
        "💳 ЮMoney\n"
        "⚡ Автоматическая доставка\n"
        "🔐 Уникальные заказы"
    )

    await callback.message.edit_text(
        text,
        reply_markup=main_menu(),
    )

    await callback.answer()


# ============================================================
# ADMIN
# ============================================================


def is_admin(user_id: int):
    return user_id in ADMIN_IDS


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
        "⚙️ <b>Панель администратора</b>\n\n"
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
        "⚙️ <b>Панель администратора</b>\n\n"
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
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: <b>{users}</b>\n"
        f"📦 Товаров: <b>{products}</b>\n"
        f"🧾 Заказов: <b>{orders}</b>\n"
        f"✅ Оплачено: <b>{paid_orders}</b>\n"
        f"💰 Выручка: "
        f"<b>{revenue:.2f} {CURRENCY}</b>"
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
            "📦 <b>Товары</b>\n\n"
            "Товаров пока нет."
        )
    else:
        text = (
            "📦 <b>Товары</b>\n\n"
        )

        for product in products:
            text += (
                f"#{product['id']} — "
                f"<b>{product['name']}</b> — "
                f"{product['price']:.2f} "
                f"{CURRENCY}\n"
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
        "➕ <b>Добавление товара</b>\n\n"
        "Введите название товара:"
    )

    await callback.answer()


@dp.message()
async def admin_input(
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

    step = state["step"]

    # NAME
    if step == "name":
        if not message.text:
            await message.answer(
                "❌ Введите название текстом."
            )
            return

        state["name"] = (
            message.text.strip()
        )

        state["step"] = "description"

        await message.answer(
            "📝 Теперь отправьте "
            "описание товара:"
        )

        return

    # DESCRIPTION
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
            "💰 Теперь отправьте цену "
            "в рублях.\n\n"
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
                "Например: <code>499</code>"
            )
            return

        state["price"] = price
        state["step"] = "file"

        await message.answer(
            "📁 Теперь отправьте файл "
            "как документ Telegram.\n\n"
            "Файл будет сохранён в Telegram, "
            "поэтому самому Render хранить "
            "копию файла не понадобится."
        )

        return

    # FILE
    if step == "file":

        if not message.document:
            await message.answer(
                "❌ Отправьте файл именно "
                "как документ Telegram."
            )
            return

        document = message.document

        product_id = add_product(
            name=state["name"],
            description=state["description"],
            price=state["price"],
            telegram_file_id=document.file_id,
            original_filename=(
                document.file_name
                or "file"
            ),
        )

        del admin_states[user_id]

        await message.answer(
            "✅ <b>Товар добавлен!</b>\n\n"
            f"🆔 ID: <code>{product_id}</code>\n"
            f"📄 {state['name']}\n"
            f"💰 {state['price']:.2f} "
            f"{CURRENCY}\n"
            f"📁 "
            f"{document.file_name or 'file'}",
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

    await callback.message.edit_text(
        "🗑 <b>Товар удалён.</b>",
        reply_markup=admin_products_keyboard(),
    )

    await callback.answer()


# ============================================================
# YOUMONEY SIGNATURE
# ============================================================


def verify_yoomoney_signature(
    data: dict,
):
    received_sign = data.get("sign")

    if not received_sign:
        return False

    params = {}

    for key, value in data.items():

        if key == "sign":
            continue

        if isinstance(value, list):
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

        encoded_value = urllib.parse.quote(
            value,
            safe="-_.~",
            encoding="utf-8",
            errors="strict",
        )

        prepared_parts.append(
            f"{key}={encoded_value}"
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
    file_id = product[
        "telegram_file_id"
    ]

    if not file_id:
        await bot.send_message(
            user_id,
            "⚠️ Оплата подтверждена, "
            "но файл товара недоступен.\n\n"
            "Администратор уведомлён.",
        )

        return

    await bot.send_message(
        user_id,
        "🎉 <b>Оплата успешно получена!</b>\n\n"
        f"📦 Товар: "
        f"<b>{product['name']}</b>\n\n"
        "📁 Отправляю файл ниже.\n\n"
        "Спасибо за покупку! ❤️",
    )

    await bot.send_document(
        user_id,
        file_id,
        caption=(
            f"📄 <b>{product['name']}</b>\n\n"
            "Спасибо за покупку!"
        ),
    )


# ============================================================
# YOUMONEY WEBHOOK
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
            "Получено уведомление ЮMoney: %s",
            {
                k: v
                for k, v in data.items()
                if k not in (
                    "sign",
                    "email",
                    "phone",
                )
            },
        )

        if not YOOMONEY_SECRET:
            logger.error(
                "YOOMONEY_SECRET не задан."
            )

            return web.Response(
                status=500,
                text="secret not configured",
            )

        if not verify_yoomoney_signature(
            data
        ):
            logger.warning(
                "Неверная подпись ЮMoney."
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

        label = data.get("label", "")

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
                "Заказ не найден: %s",
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
            amount_received = 0

        expected_amount = float(
            order["amount"]
        )

        if (
            amount_received + 0.0001
            < expected_amount
        ):
            logger.warning(
                "Недостаточная сумма. "
                "Получено: %s, нужно: %s",
                amount_received,
                expected_amount,
            )

            await bot.send_message(
                order["user_id"],
                "⚠️ <b>Платёж получен, "
                "но сумма отличается.</b>\n\n"
                f"Получено: "
                f"{amount_received:.2f} "
                f"{CURRENCY}\n"
                f"Нужно: "
                f"{expected_amount:.2f} "
                f"{CURRENCY}\n\n"
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

            logger.error(
                "Товар не найден: %s",
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

        try:

            await send_product_to_user(
                paid_order["user_id"],
                product,
            )

        except Exception as error:

            logger.exception(
                "Ошибка отправки файла: %s",
                error,
            )

            await bot.send_message(
                paid_order["user_id"],
                "⚠️ Платёж подтверждён, "
                "но при отправке файла "
                "произошла ошибка.\n\n"
                "Администратор уведомлён.",
            )

        # Уведомление администратора
        for admin_id in ADMIN_IDS:

            try:

                await bot.send_message(
                    admin_id,
                    "💰 <b>Новая оплата!</b>\n\n"
                    f"🧾 Заказ: "
                    f"<code>{paid_order['id']}</code>\n"
                    f"📦 Товар: "
                    f"<b>{product['name']}</b>\n"
                    f"💰 Сумма: "
                    f"<b>{amount_received:.2f} "
                    f"{CURRENCY}</b>\n"
                    f"👤 User ID: "
                    f"<code>{paid_order['user_id']}</code>\n"
                    f"🔑 Label: "
                    f"<code>{label}</code>\n"
                    f"🆔 Operation: "
                    f"<code>{operation_id}</code>",
                )

            except Exception:
                logger.exception(
                    "Не удалось отправить "
                    "уведомление админу."
                )

        return web.Response(
            status=200,
            text="ok",
        )

    except Exception as error:

        logger.exception(
            "Ошибка ЮMoney webhook: %s",
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
        text="Digital Store Bot is running.",
    )


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================


async def telegram_webhook(
    request: web.Request,
):
    try:

        update = await request.json()

        await dp.feed_raw_update(
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
# WEB SERVER
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
        "/yoomoney",
        yoomoney_webhook,
    )

    app.router.add_post(
        TELEGRAM_WEBHOOK_PATH,
        telegram_webhook,
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
# SET TELEGRAM WEBHOOK
# ============================================================


async def setup_telegram_webhook():

    if not WEBHOOK_BASE_URL:
        raise RuntimeError(
            "WEBHOOK_BASE_URL не задан."
        )

    webhook_url = (
        WEBHOOK_BASE_URL
        + TELEGRAM_WEBHOOK_PATH
    )

    logger.info(
        "Setting Telegram webhook: %s",
        webhook_url,
    )

    await bot.set_webhook(
        webhook_url,
        drop_pending_updates=True,
    )

    info = await bot.get_webhook_info()

    logger.info(
        "Telegram webhook URL: %s",
        info.url,
    )


# ============================================================
# VALIDATE CONFIG
# ============================================================


def validate_config():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан в Render."
        )

    if (
        YOOMONEY_WALLET
        == "41001XXXXXXXXXXXX"
    ):
        raise RuntimeError(
            "Укажи настоящий "
            "YOOMONEY_WALLET."
        )

    if not YOOMONEY_SECRET:
        raise RuntimeError(
            "YOOMONEY_SECRET не задан."
        )

    if not ADMIN_IDS:
        raise RuntimeError(
            "ADMIN_IDS не задан."
        )

    if not WEBHOOK_BASE_URL:
        raise RuntimeError(
            "WEBHOOK_BASE_URL не задан."
        )


# ============================================================
# START
# ============================================================


async def main():

    validate_config()

    init_db()

    web_runner = (
        await start_web_server()
    )

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
            "Telegram webhook: %s%s",
            WEBHOOK_BASE_URL,
            TELEGRAM_WEBHOOK_PATH,
        )

        logger.info(
            "ЮMoney webhook: %s/yoomoney",
            WEBHOOK_BASE_URL,
        )

        logger.info(
            "================================="
        )

        # Webhook работает через aiohttp.
        # Просто держим процесс запущенным.
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
            "Bot stopped."
        )
