# ============================================================
# TELEGRAM DIGITAL STORE + ЮMONEY
# Python 3.10+
#
# Установка:
#   pip install aiogram aiohttp
#
# Запуск:
#   python bot.py
#
# ВАЖНО:
# 1. Заполни BOT_TOKEN, YOOMONEY_WALLET и YOOMONEY_SECRET.
# 2. Для автоматической оплаты нужен HTTPS webhook:
#       https://YOUR_DOMAIN/yoomoney
# 3. В настройках HTTP-уведомлений ЮMoney укажи этот URL.
#
# Структура:
#   bot.py
#   files/                 <- сюда можно класть продаваемые файлы
#   store.db               <- создаётся автоматически
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
from datetime import datetime
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
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = "8777780924:AAFQg_KopPFIls-FZ-2pQN-Iq38IKL31rks"

# Номер кошелька ЮMoney
YOOMONEY_WALLET = "41001XXXXXXXXXXXX"

# Секрет из:
# ЮMoney -> Настройки -> HTTP-уведомления -> Показать секрет
YOOMONEY_SECRET = "PASTE_YOOMONEY_SECRET_HERE"

# Публичный HTTPS адрес сервера.
# Пример:
# https://example.com/yoomoney
YOOMONEY_WEBHOOK_URL = "https://YOUR_DOMAIN/yoomoney"

# Порт локального веб-сервера
WEB_PORT = 8080

# ID администраторов Telegram.
# Узнать свой ID можно через @userinfobot.
ADMIN_IDS = {
    8346538289,
}

# Папка с файлами
FILES_DIR = Path("files")
FILES_DIR.mkdir(exist_ok=True)

# База данных
DB_FILE = "store.db"

# Название магазина
SHOP_NAME = "Digital Market"

# Валюта
CURRENCY = "₽"

# ============================================================
# ЛОГИ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)

# ============================================================
# DATABASE
# ============================================================


def db():
    conn = sqlite3.connect(DB_FILE)
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


def save_user(user_id: int, username: str | None, first_name: str | None):
    conn = db()

    conn.execute(
        """
        INSERT INTO users(user_id, username, first_name, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
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


def get_products():
    conn = db()
    rows = conn.execute(
        "SELECT * FROM products ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return rows


def get_product(product_id: int):
    conn = db()
    row = conn.execute(
        "SELECT * FROM products WHERE id=?",
        (product_id,),
    ).fetchone()
    conn.close()
    return row


def add_product(
    name: str,
    description: str,
    price: float,
    file_path: str,
):
    conn = db()

    cur = conn.execute(
        """
        INSERT INTO products
        (name, description, price, file_path, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            name,
            description,
            price,
            file_path,
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
        "DELETE FROM products WHERE id=?",
        (product_id,),
    )
    conn.commit()
    conn.close()


def create_order(
    user_id: int,
    username: str | None,
    product_id: int,
    amount: float,
):
    # label должен быть уникальным.
    label = f"TG-{secrets.token_hex(8).upper()}"

    conn = db()

    cur = conn.execute(
        """
        INSERT INTO orders
        (user_id, username, product_id, amount, label, status, created_at)
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
        "SELECT * FROM orders WHERE label=?",
        (label,),
    ).fetchone()

    conn.close()

    return row


def mark_order_paid(label: str, operation_id: str):
    conn = db()

    row = conn.execute(
        """
        SELECT * FROM orders
        WHERE label=? AND status='pending'
        """,
        (label,),
    ).fetchone()

    if not row:
        conn.close()
        return None

    conn.execute(
        """
        UPDATE orders
        SET status='paid',
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
        "SELECT COUNT(*) FROM orders WHERE status='paid'"
    ).fetchone()[0]

    revenue = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM orders
        WHERE status='paid'
        """
    ).fetchone()[0]

    conn.close()

    return users, products, orders, paid_orders, revenue


# ============================================================
# TELEGRAM
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
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
                ),
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="catalog",
                ),
            ],
        ]
    )


def payment_keyboard(label: str):
    payment_url = (
        "https://yoomoney.ru/quickpay/confirm?"
        + urllib.parse.urlencode(
            {
                "receiver": YOOMONEY_WALLET,
                "quickpay-form": "button",
                "sum": "0",
                "paymentType": "AC",
                "label": label,
            }
        )
    )

    # Сумма будет заменена ниже непосредственно при создании заказа.
    return payment_url


def admin_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить товар",
                    callback_data="admin_add",
                ),
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
        ]
    )


def admin_products_keyboard():
    rows = []

    products = get_products()

    for product in products:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 {product['name']}",
                    callback_data=f"delete:{product['id']}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="admin",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
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
        "Добро пожаловать в магазин цифровых товаров.\n\n"
        "📁 Мгновенная выдача файлов\n"
        "💳 Оплата через ЮMoney\n"
        "🔐 Безопасная обработка заказов\n"
        "⚡ Получение товара сразу после оплаты\n\n"
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
async def catalog(callback: CallbackQuery):
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
                        f"{product['price']:.2f} {CURRENCY}"
                    ),
                    callback_data=f"product:{product['id']}",
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


@dp.callback_query(F.data.startswith("product:"))
async def product_view(callback: CallbackQuery):
    product_id = int(callback.data.split(":")[1])

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
        f"💰 Цена: <b>{product['price']:.2f} {CURRENCY}</b>\n"
        f"⚡ Выдача: <b>автоматически</b>\n\n"
        "Нажмите «Купить», чтобы перейти к оплате."
    )

    await callback.message.edit_text(
        text,
        reply_markup=product_keyboard(product_id),
    )

    await callback.answer()


# ============================================================
# BUY
# ============================================================


@dp.callback_query(F.data.startswith("buy:"))
async def buy(callback: CallbackQuery):
    if not callback.from_user:
        return

    product_id = int(callback.data.split(":")[1])

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

    # Формируем официальный URL формы ЮMoney.
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
                    callback_data=f"check:{label}",
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
        f"📦 Товар: <b>{product['name']}</b>\n"
        f"💰 Сумма: <b>{product['price']:.2f} {CURRENCY}</b>\n"
        f"🧾 Заказ: <code>#{order_id}</code>\n\n"
        "1️⃣ Нажмите кнопку оплаты.\n"
        "2️⃣ Оплатите заказ на странице ЮMoney.\n"
        "3️⃣ После подтверждения платежа файл будет отправлен автоматически.\n\n"
        "⚠️ <b>Не передавайте ссылку на оплату другим людям.</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
    )

    await callback.answer()


# ============================================================
# MANUAL CHECK
# ============================================================


@dp.callback_query(F.data.startswith("check:"))
async def check_payment(callback: CallbackQuery):
    label = callback.data.split(":", 1)[1]

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
            "Оплата уже подтверждена.",
            show_alert=True,
        )
        return

    await callback.answer(
        "⏳ Платёж ещё не получил подтверждение. "
        "Если вы только что оплатили — подождите несколько секунд.",
        show_alert=True,
    )


# ============================================================
# PURCHASES
# ============================================================


@dp.callback_query(F.data == "purchases")
async def purchases(callback: CallbackQuery):
    rows = get_user_orders(callback.from_user.id)

    if not rows:
        text = (
            "📦 <b>Мои покупки</b>\n\n"
            "У вас пока нет заказов."
        )

        await callback.message.edit_text(
            text,
            reply_markup=main_menu(),
        )

        await callback.answer()
        return

    text = "📦 <b>Мои покупки</b>\n\n"

    for row in rows[:15]:
        status = (
            "✅ Оплачен"
            if row["status"] == "paid"
            else "⏳ Ожидает оплаты"
        )

        text += (
            f"• <b>{row['product_name'] or 'Товар'}</b>\n"
            f"  💰 {row['amount']:.2f} {CURRENCY}\n"
            f"  {status}\n"
            f"  🧾 <code>{row['label']}</code>\n\n"
        )

    await callback.message.edit_text(
        text,
        reply_markup=main_menu(),
    )

    await callback.answer()


# ============================================================
# HOW
# ============================================================


@dp.callback_query(F.data == "how")
async def how(callback: CallbackQuery):
    text = (
        "💎 <b>Как всё работает</b>\n\n"
        "🛍 <b>1. Выбираете товар</b>\n"
        "Открываете каталог и выбираете нужный файл.\n\n"
        "💳 <b>2. Оплачиваете</b>\n"
        "Бот создаёт уникальный заказ и отправляет вас "
        "на защищённую страницу ЮMoney.\n\n"
        "🔐 <b>3. ЮMoney подтверждает оплату</b>\n"
        "Платёж идентифицируется по уникальной метке заказа.\n\n"
        "📁 <b>4. Получаете файл</b>\n"
        "После подтверждения оплаты бот автоматически "
        "отправляет купленный файл.\n\n"
        "⚡ Всё происходит без участия администратора."
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


@dp.callback_query(F.data == "support")
async def support(callback: CallbackQuery):
    text = (
        "💬 <b>Поддержка</b>\n\n"
        "Если возникла проблема с оплатой или получением файла, "
        "обратитесь к администратору магазина.\n\n"
        "🕐 Обычно ответы приходят максимально быстро."
    )

    await callback.message.edit_text(
        text,
        reply_markup=main_menu(),
    )

    await callback.answer()


# ============================================================
# HOME
# ============================================================


@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery):
    text = (
        f"✨ <b>{SHOP_NAME}</b>\n\n"
        "Цифровые товары с мгновенной выдачей.\n\n"
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
async def admin(message: Message):
    if not message.from_user:
        return

    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return

    await message.answer(
        "⚙️ <b>Панель администратора</b>\n\n"
        "Выберите действие:",
        reply_markup=admin_menu(),
    )


@dp.callback_query(F.data == "admin")
async def admin_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
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


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True,
        )
        return

    users, products, orders, paid_orders, revenue = statistics()

    text = (
        "📊 <b>Статистика магазина</b>\n\n"
        f"👥 Пользователей: <b>{users}</b>\n"
        f"📦 Товаров: <b>{products}</b>\n"
        f"🧾 Заказов: <b>{orders}</b>\n"
        f"✅ Оплачено: <b>{paid_orders}</b>\n"
        f"💰 Выручка: <b>{revenue:.2f} {CURRENCY}</b>\n"
    )

    await callback.message.edit_text(
        text,
        reply_markup=admin_menu(),
    )

    await callback.answer()


@dp.callback_query(F.data == "admin_products")
async def admin_products(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
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
        text = "📦 <b>Товары</b>\n\n"

        for product in products:
            text += (
                f"#{product['id']} — "
                f"<b>{product['name']}</b> — "
                f"{product['price']:.2f} {CURRENCY}\n"
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


@dp.callback_query(F.data == "admin_add")
async def admin_add(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True,
        )
        return

    admin_states[callback.from_user.id] = {
        "step": "name"
    }

    await callback.message.edit_text(
        "➕ <b>Добавление товара</b>\n\n"
        "Введите название товара:"
    )

    await callback.answer()


@dp.message()
async def admin_input(message: Message):
    if not message.from_user:
        return

    user_id = message.from_user.id

    if not is_admin(user_id):
        return

    state = admin_states.get(user_id)

    if not state:
        return

    step = state["step"]

    # -----------------------------
    # NAME
    # -----------------------------

    if step == "name":
        state["name"] = message.text.strip()
        state["step"] = "description"

        await message.answer(
            "📝 Теперь отправьте описание товара:"
        )

        return

    # -----------------------------
    # DESCRIPTION
    # -----------------------------

    if step == "description":
        state["description"] = message.text.strip()
        state["step"] = "price"

        await message.answer(
            "💰 Теперь отправьте цену в рублях.\n\n"
            "Например:\n"
            "<code>499</code>"
        )

        return

    # -----------------------------
    # PRICE
    # -----------------------------

    if step == "price":
        try:
            price = float(
                message.text.replace(",", ".")
            )

            if price <= 0:
                raise ValueError

        except ValueError:
            await message.answer(
                "❌ Неверная цена.\n"
                "Введите число, например: <code>499</code>"
            )
            return

        state["price"] = price
        state["step"] = "file"

        await message.answer(
            "📁 Теперь отправьте файл, который будет "
            "выдаваться покупателю после оплаты."
        )

        return

    # -----------------------------
    # FILE
    # -----------------------------

    if step == "file":
        if not message.document:
            await message.answer(
                "❌ Нужно отправить именно файл "
                "как документ Telegram."
            )
            return

        document = message.document

        try:
            tg_file = await bot.get_file(
                document.file_id
            )

            safe_name = (
                f"{secrets.token_hex(6)}_"
                f"{document.file_name or 'file.bin'}"
            )

            destination = FILES_DIR / safe_name

            await bot.download_file(
                tg_file.file_path,
                destination=destination,
            )

        except Exception as e:
            logger.exception(
                "Ошибка загрузки файла: %s",
                e,
            )

            await message.answer(
                "❌ Не удалось сохранить файл."
            )

            return

        product_id = add_product(
            name=state["name"],
            description=state["description"],
            price=state["price"],
            file_path=str(destination),
        )

        del admin_states[user_id]

        await message.answer(
            "✅ <b>Товар успешно добавлен!</b>\n\n"
            f"🆔 ID: <code>{product_id}</code>\n"
            f"📄 {state['name']}\n"
            f"💰 {state['price']:.2f} {CURRENCY}\n"
            f"📁 {document.file_name}"
        )

        return


# ============================================================
# ADMIN DELETE
# ============================================================


@dp.callback_query(F.data.startswith("delete:"))
async def delete_product_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True,
        )
        return

    product_id = int(callback.data.split(":")[1])

    product = get_product(product_id)

    if not product:
        await callback.answer(
            "Товар уже удалён.",
            show_alert=True,
        )
        return

    delete_product(product_id)

    try:
        path = Path(product["file_path"])

        if path.exists():
            path.unlink()

    except Exception:
        logger.exception(
            "Не удалось удалить файл товара."
        )

    await callback.message.edit_text(
        "🗑 <b>Товар удалён.</b>",
        reply_markup=admin_products_keyboard(),
    )

    await callback.answer()


# ============================================================
# ЮMONEY SIGN VERIFICATION
# ============================================================


def verify_yoomoney_signature(data: dict):
    """
    Новая схема ЮMoney:
    HMAC-SHA256 от URL-кодированной строки всех параметров,
    кроме sign, отсортированных по имени.
    """

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
        key=lambda x: x[0],
    )

    prepared = "&".join(
        f"{urllib.parse.quote(str(k), safe='-_.~')}="
        f"{urllib.parse.quote(str(v), safe='-_.~')}"
        for k, v in sorted_items
    )

    expected = hmac.new(
        YOOMONEY_SECRET.encode("utf-8"),
        prepared.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(
        expected.lower(),
        str(received_sign).lower(),
    )


# ============================================================
# SEND DIGITAL PRODUCT
# ============================================================


async def send_product_to_user(
    user_id: int,
    product,
):
    file_path = Path(product["file_path"])

    if not file_path.exists():
        await bot.send_message(
            user_id,
            "⚠️ Оплата подтверждена, "
            "но файл товара временно недоступен.\n\n"
            "Администратор уже уведомлён.",
        )

        logger.error(
            "Файл не найден: %s",
            file_path,
        )

        return

    await bot.send_message(
        user_id,
        "🎉 <b>Оплата успешно получена!</b>\n\n"
        f"📦 Ваш товар: <b>{product['name']}</b>\n\n"
        "📁 Отправляю файл ниже.\n"
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
            "Спасибо за покупку!"
        ),
    )


# ============================================================
# ЮMONEY WEBHOOK
# ============================================================


async def yoomoney_webhook(request: web.Request):
    try:
        post = await request.post()

        data = {
            key: value
            for key, value in post.items()
        }

        logger.info(
            "Получено уведомление ЮMoney: %s",
            data,
        )

        # Проверяем подпись.
        if not verify_yoomoney_signature(data):
            logger.warning(
                "Неверная подпись ЮMoney."
            )

            return web.Response(
                status=403,
                text="invalid signature",
            )

        # Проверяем тип уведомления.
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

        order = get_order_by_label(label)

        if not order:
            logger.warning(
                "Заказ с label %s не найден.",
                label,
            )

            return web.Response(
                status=200,
                text="unknown order",
            )

        # Уже обработан.
        if order["status"] == "paid":
            return web.Response(
                status=200,
                text="already paid",
            )

        # Сумма из уведомления.
        try:
            amount_received = float(
                data.get("amount", "0")
            )
        except ValueError:
            amount_received = 0

        expected_amount = float(
            order["amount"]
        )

        # Нельзя выдавать товар при недостаточной сумме.
        if amount_received + 0.0001 < expected_amount:
            logger.warning(
                "Недостаточная сумма: "
                "получено=%s, нужно=%s",
                amount_received,
                expected_amount,
            )

            await bot.send_message(
                order["user_id"],
                "⚠️ <b>Платёж получен, "
                "но сумма отличается от суммы заказа.</b>\n\n"
                f"Получено: {amount_received:.2f} {CURRENCY}\n"
                f"Нужно: {expected_amount:.2f} {CURRENCY}\n\n"
                "Обратитесь в поддержку.",
            )

            return web.Response(
                status=200,
                text="amount mismatch",
            )

        # Проверяем уникальность операции.
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
                "Товар заказа не найден: %s",
                paid_order["product_id"],
            )

            await bot.send_message(
                paid_order["user_id"],
                "⚠️ Платёж подтверждён, "
                "но товар не найден.\n"
                "Свяжитесь с поддержкой.",
            )

            return web.Response(
                status=200,
                text="product missing",
            )

        # Выдаём файл.
        try:
            await send_product_to_user(
                paid_order["user_id"],
                product,
            )

        except Exception as e:
            logger.exception(
                "Ошибка отправки товара: %s",
                e,
            )

            await bot.send_message(
                paid_order["user_id"],
                "⚠️ Платёж подтверждён, "
                "но возникла ошибка при отправке файла.\n\n"
                "Администратор уведомлён.",
            )

        # Уведомление админу.
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    "💰 <b>Новая оплата!</b>\n\n"
                    f"🧾 Заказ: <code>{paid_order['id']}</code>\n"
                    f"📦 Товар: <b>{product['name']}</b>\n"
                    f"💰 Сумма: <b>{amount_received:.2f} {CURRENCY}</b>\n"
                    f"👤 User ID: <code>{paid_order['user_id']}</code>\n"
                    f"🔑 Label: <code>{label}</code>\n"
                    f"🆔 Operation: <code>{operation_id}</code>",
                )
            except Exception:
                pass

        return web.Response(
            status=200,
            text="ok",
        )

    except Exception as e:
        logger.exception(
            "Ошибка webhook ЮMoney: %s",
            e,
        )

        # Лучше вернуть 200 после разбора запроса,
        # чтобы не получить бесконечные повторные уведомления
        # при внутренней ошибке.
        return web.Response(
            status=200,
            text="error",
        )


# ============================================================
# HEALTHCHECK
# ============================================================


async def health(request: web.Request):
    return web.Response(
        status=200,
        text="Digital Store Bot is running.",
    )


# ============================================================
# WEB SERVER
# ============================================================


async def start_web_server():
    app = web.Application()

    app.router.add_post(
        "/yoomoney",
        yoomoney_webhook,
    )

    app.router.add_get(
        "/health",
        health,
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=WEB_PORT,
    )

    await site.start()

    logger.info(
        "Web server started on port %s",
        WEB_PORT,
    )

    return runner


# ============================================================
# STARTUP
# ============================================================


async def main():
    if BOT_TOKEN.startswith("PASTE_"):
        raise RuntimeError(
            "Укажи BOT_TOKEN в начале файла."
        )

    if YOOMONEY_WALLET.startswith("41001X"):
        raise RuntimeError(
            "Укажи настоящий YOOMONEY_WALLET."
        )

    if YOOMONEY_SECRET.startswith("PASTE_"):
        raise RuntimeError(
            "Укажи YOOMONEY_SECRET."
        )

    if not ADMIN_IDS:
        raise RuntimeError(
            "Добавь свой Telegram ID в ADMIN_IDS."
        )

    init_db()

    web_runner = await start_web_server()

    try:
        logger.info(
            "Bot started: %s",
            SHOP_NAME,
        )

        await dp.start_polling(bot)

    finally:
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
            "Bot stopped by user."
        )
