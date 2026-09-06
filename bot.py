import asyncio
import os
from io import BytesIO  # [NEW] картинка диаграммы в памяти, без сохранения на диск
from pathlib import Path
import datetime
import calendar



import matplotlib  # [NEW] библиотека для круговой диаграммы

matplotlib.use("Agg")  # [NEW] режим без окна — бот рисует график в фоне
import matplotlib.pyplot as plt  # [NEW] построение pie-chart
from matplotlib import font_manager  # [NEW] шрифт с кириллицей для подписей

from aiogram import Bot, Dispatcher, F  # [CHG] F — фильтр «только фото» для загрузки чека
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    BotCommand,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BufferedInputFile,  # [NEW] отправка PNG диаграммы в Telegram
)

from aiogram.types import (
    Message,
    CallbackQuery,
    BotCommand,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

from vision import recognize_receipt
from vision import get_ai_savings_advice # Импортируем новую функцию (сейчас создадим её в vision.py)
from vision import get_ai_forecast_verdict

from database import (
    init_db,
    add_group,
    add_member,
    get_members,
    add_receipt,
    get_receipts,
    get_last_receipt,
    get_receipt_path,
    link_member_to_receipt,
    add_item,
    update_receipt_total,
    add_debt,
    get_debts,
    get_all_debts,
    clear_debts,
    clear_members,
    get_my_receipts,
    get_my_debts,
    get_receipt_items,
    get_receipt_participants,
    get_receipt_item_participants,
    set_receipt_payer,
    get_category_totals_last_week,  # [CHG] суммы по категориям за последнюю неделю
    clear_expenses,  # [NEW] удаление чеков, товаров и связанных долгов группы
    link_member_to_item,
    get_budget_forecast_data,
)

load_dotenv()
 
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Время жизни сообщений с меню выбора.
BUTTON_MENU_DELETE_DELAY = 20


def schedule_button_message_delete(message: Message, delay: int = BUTTON_MENU_DELETE_DELAY):
    """Удаляет сообщение с inline-кнопками через delay секунд, если его ещё не убрали."""
    async def _delete_later():
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except Exception:
            # Сообщение могло быть удалено раньше после нажатия кнопки.
            pass

    asyncio.create_task(_delete_later())


# =========================================================
# СОСТОЯНИЯ
# =========================================================

class ReceiptState(StatesGroup):
    waiting_receipt_photo = State()  # [NEW] ждём фото только после кнопки «Загрузить чек»
    choosing_members = State()
    receipt_category = State()  # [NEW] выбор категории после распознавания чека
    receipt_category_custom = State()  # [NEW] своя категория для чека
    waiting_confirmation = State()
    manual_amount = State()
    manual_category = State()
    manual_category_custom = State()
    manual_payer = State()
    manual_participants = State()
    manual_confirmation = State()
    waiting_ai_advice = State()


# =========================================================
# ПАПКА ДЛЯ ЧЕКОВ
# =========================================================

RECEIPTS_DIR = Path("receipts")
RECEIPTS_DIR.mkdir(exist_ok=True)


# =========================================================
# ГЛАВНОЕ МЕНЮ
# =========================================================

def main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="👥 Участники"),
                KeyboardButton(text="➕ Присоединиться"),
            ],
            [
                KeyboardButton(text="💸 Долги"),
                KeyboardButton(text="🧾 История покупок"),
            ],
            [
                KeyboardButton(text="🧹 Очистить"),
                KeyboardButton(text="📊 Категории"),
            ],
            [
                KeyboardButton(text="➕ Добавить покупку"),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )
    return keyboard


# =========================================================
# КОМАНДЫ TELEGRAM
# =========================================================

async def setup_bot_commands():
    commands = [
        BotCommand(
            command="start",
            description="🏠 Главное меню"
        ),
        BotCommand(
            command="join",
            description="➕ Присоединиться к группе"
        ),
        BotCommand(
            command="members",
            description="👥 Показать участников"
        ),
        BotCommand(
            command="receipts",
            description="🧾 Показать чеки"
        ),
        BotCommand(
            command="debts",
            description="💸 Показать долги"
        ),
        BotCommand(  # [NEW] команда меню Telegram для диаграммы категорий
            command="categories",
            description="📊 Расходы по категориям"
        ),
    ]

    await bot.set_my_commands(commands)


# =========================================================
# /start
# =========================================================

async def send_main_menu(message: Message):
    """Показывает актуальное главное меню и принудительно обновляет Reply Keyboard."""
    await message.answer(
        "🏠 <b>Главное меню</b>\n\nВыберите действие:",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )

@dp.message(CommandStart())
async def start(message: Message):
    await send_main_menu(message)



# =========================================================
# /join
# =========================================================

@dp.message(Command("join"))
async def join_group(message: Message):
    if message.chat.type == "private":
        await message.answer(
            "❌ Эту команду нужно использовать в Telegram-группе."
        )
        return

    await add_group(
        chat_id=message.chat.id,
        title=message.chat.title or "Без названия"
    )

    await add_member(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )

    await message.answer(
        f"✅ {message.from_user.first_name}, "
        "ты добавлен в группу расходов!"
    )


# =========================================================
# КНОПКА "ПРИСОЕДИНИТЬСЯ"
# =========================================================

@dp.message(lambda message: message.text == "➕ Присоединиться")
async def join_button(message: Message):
    await join_group(message)


# =========================================================
# /members
# =========================================================

@dp.message(Command("members"))
async def members(message: Message):
    if message.chat.type == "private":
        await message.answer(
            "❌ Эту команду нужно использовать в группе."
        )
        return

    members_list = await get_members(message.chat.id)

    if not members_list:
        await message.answer(
            "👥 Пока никто не добавился.\n\n"
            "Нажми «➕ Присоединиться»."
        )
        return

    text = "👥 Участники группы:\n\n"

    for number, member in enumerate(members_list, start=1):
        user_id, username, first_name = member

        if username:
            name = f"{first_name} (@{username})"
        else:
            name = first_name

        text += f"{number}. {name}\n"

    await message.answer(text)


# =========================================================
# КНОПКА "УЧАСТНИКИ"
# =========================================================

@dp.message(lambda message: message.text == "👥 Участники")
async def members_button(message: Message):
    await members(message)


# =========================================================
# РУЧНОЕ ДОБАВЛЕНИЕ ПОКУПКИ (F2)
# =========================================================

MANUAL_CATEGORIES = [
    "Продукты",
    "Коммуналка",
    "Транспорт",
    "Развлечения",
    "Прочее",
    "✏️ Своя категория",
]


def purchase_categories_keyboard(callback_prefix: str):  # [NEW] один список категорий для ручного ввода и для чека
    builder = InlineKeyboardBuilder()  # [NEW]
    for category in MANUAL_CATEGORIES:  # [NEW]
        builder.button(text=category, callback_data=f"{callback_prefix}:{category}")  # [NEW]
    builder.adjust(2)  # [NEW]
    return builder.as_markup()  # [NEW]


@dp.message(lambda message: message.text == "➕ Добавить покупку")
async def add_purchase_menu(message: Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="📷 Сканировать чек", callback_data="add_purchase:scan")
    builder.button(text="✍️ Ручной ввод", callback_data="add_purchase:manual")
    builder.button(text="⬅️ Назад", callback_data="add_purchase:back")
    builder.adjust(1)
    sent = await message.answer("➕ <b>Добавить покупку</b>\n\nВыберите способ:", reply_markup=builder.as_markup(), parse_mode="HTML")
    schedule_button_message_delete(sent)


@dp.callback_query(lambda c: c.data == "add_purchase:back")
async def add_purchase_back(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()


@dp.callback_query(lambda c: c.data == "add_purchase:scan")
async def add_purchase_scan(callback: CallbackQuery, state: FSMContext):
    if callback.message.chat.type == "private":
        await callback.answer("Сканировать чек нужно в группе.", show_alert=True)
        return
    await state.clear()
    await state.set_state(ReceiptState.waiting_receipt_photo)
    await callback.message.edit_text("📷 <b>Сканирование чека</b>\n\nОтправьте фотографию чека в этот чат.")
    await callback.answer()


@dp.callback_query(lambda c: c.data == "add_purchase:manual")
async def manual_purchase_start_callback(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await manual_purchase_start(callback.message, state)
    await callback.answer()


@dp.message(lambda message: message.text == "✍️ Ручной ввод")
async def manual_purchase_start(message: Message, state: FSMContext):
    if message.chat.type == "private":
        await message.answer("❌ Ручную покупку нужно добавлять в группе.")
        return

    members = await get_members(message.chat.id)
    if not members:
        await message.answer(
            "❌ В группе пока нет участников.\n\n"
            "Сначала нажмите «➕ Присоединиться»."
        )
        return

    await state.clear()
    await state.set_state(ReceiptState.manual_amount)
    await message.answer(
        "✍️ <b>Добавление покупки вручную</b>\n\n"
        "Введите сумму покупки в рублях.\n"
        "Например: <code>1250.50</code> или <code>1250,50</code>",
        parse_mode="HTML"
    )


@dp.message(ReceiptState.manual_amount)
async def manual_amount(message: Message, state: FSMContext):
    text = (message.text or "").strip().replace(",", ".").replace(" ", "")
    try:
        amount = float(text)
    except ValueError:
        await message.answer("❌ Не понял сумму. Введите число, например <code>850.50</code>.", parse_mode="HTML")
        return

    if amount <= 0:
        await message.answer("❌ Сумма должна быть больше нуля.")
        return

    if amount > 10_000_000:
        await message.answer("❌ Сумма слишком большая. Проверьте ввод.")
        return

    await state.update_data(manual_amount=round(amount, 2))
    await state.set_state(ReceiptState.manual_category)  # [CHG] категория через общую клавиатуру
    sent = await message.answer(  # [CHG]
        "🏷 Выберите категорию покупки:",  # [CHG]
        reply_markup=purchase_categories_keyboard("manual_cat")  # [CHG] тот же список, что на скриншоте
    )
    schedule_button_message_delete(sent)


@dp.callback_query(ReceiptState.manual_category, lambda c: c.data.startswith("manual_cat:"))
async def manual_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    try:
        await callback.message.delete()
    except Exception:
        pass

    if category == "✏️ Своя категория":
        await state.set_state(ReceiptState.manual_category_custom)
        await callback.message.answer(
            "✏️ Введите свою категорию одним сообщением.\n"
            "Например: «Дом», «Аптека» или «Питомцы»."
        )
        await callback.answer()
        return

    await state.update_data(manual_category=category)
    await show_manual_payer(callback.message, state)
    await callback.answer()


@dp.message(ReceiptState.manual_category_custom)
async def manual_custom_category(message: Message, state: FSMContext):
    category = (message.text or "").strip()
    if not category or len(category) > 50:
        await message.answer("❌ Категория должна содержать от 1 до 50 символов.")
        return

    # [FIX] Принудительно капитализируем первую букву (например: "аптека" -> "Аптека"), 
    # чтобы на графике не плодились одинаковые категории с разным регистром
    category = category.capitalize() 

    await state.update_data(manual_category=category) # [CHG] Сохраняем обработанную строку
    await show_manual_payer(message, state)


async def show_manual_payer(message: Message, state: FSMContext):
    members = await get_members(message.chat.id)
    builder = InlineKeyboardBuilder()

    for user_id, username, first_name in members:
        name = first_name or (f"@{username}" if username else str(user_id))
        builder.button(text=f"💳 {name}", callback_data=f"manual_payer:{user_id}")
    builder.adjust(1)

    await state.set_state(ReceiptState.manual_payer)
    sent = await message.answer(
        "💳 Кто оплатил покупку?",
        reply_markup=builder.as_markup()
    )
    schedule_button_message_delete(sent)


@dp.callback_query(ReceiptState.manual_payer, lambda c: c.data.startswith("manual_payer:"))
async def manual_payer(callback: CallbackQuery, state: FSMContext):
    payer_id = int(callback.data.split(":", 1)[1])
    try:
        await callback.message.delete()
    except Exception:
        pass
    await state.update_data(manual_payer=payer_id, manual_selected=[])

    await show_manual_participants(callback.message, state)
    await callback.answer()


async def show_manual_participants(message: Message, state: FSMContext):
    data = await state.get_data()
    selected = set(data.get("manual_selected", []))
    members = await get_members(message.chat.id)

    builder = InlineKeyboardBuilder()
    for user_id, username, first_name in members:
        mark = "✅" if user_id in selected else "⬜"
        name = first_name or (f"@{username}" if username else str(user_id))
        builder.button(
            text=f"{mark} {name}",
            callback_data=f"manual_member:{user_id}"
        )

    builder.button(text="✅ Готово", callback_data="manual_members_done")
    builder.adjust(2)

    await state.set_state(ReceiptState.manual_participants)
    await message.answer(
        "👥 Кто участвует в покупке?\n\n"
        "Выберите одного или нескольких участников. "
        "Оплачивающий тоже может быть выбран.",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(ReceiptState.manual_participants, lambda c: c.data.startswith("manual_member:"))
async def manual_toggle_participant(callback: CallbackQuery, state: FSMContext):
    uid = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    selected = set(data.get("manual_selected", []))

    if uid in selected:
        selected.remove(uid)
    else:
        selected.add(uid)

    await state.update_data(manual_selected=list(selected))
    await callback.message.edit_reply_markup(
        reply_markup=await manual_participants_markup(callback.message.chat.id, selected)
    )
    await callback.answer()


async def manual_participants_markup(chat_id: int, selected):
    members = await get_members(chat_id)
    builder = InlineKeyboardBuilder()

    for user_id, username, first_name in members:
        mark = "✅" if user_id in selected else "⬜"
        name = first_name or (f"@{username}" if username else str(user_id))
        builder.button(
            text=f"{mark} {name}",
            callback_data=f"manual_member:{user_id}"
        )

    builder.button(text="✅ Готово", callback_data="manual_members_done")
    builder.adjust(2)
    return builder.as_markup()


@dp.callback_query(ReceiptState.manual_participants, lambda c: c.data == "manual_members_done")
async def manual_members_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("manual_selected", [])

    if not selected:
        await callback.answer("Выберите хотя бы одного участника.", show_alert=True)
        return

    amount = float(data["manual_amount"])
    category = data["manual_category"]
    payer = int(data["manual_payer"])
    share = round(amount / len(selected), 2)

    members = await get_members(callback.message.chat.id)
    names = {
        uid: (first_name or (f"@{username}" if username else str(uid)))
        for uid, username, first_name in members
    }

    selected_names = ", ".join(names.get(uid, str(uid)) for uid in selected)
    payer_name = names.get(payer, "Неизвестный")

    await state.set_state(ReceiptState.manual_confirmation)
    await callback.message.edit_text(
        "✍️ <b>Проверьте покупку</b>\n\n"
        f"💰 Сумма: {amount:.2f} ₽\n"
        f"🏷 Категория: {category}\n"
        f"💳 Оплатил: {payer_name}\n"
        f"👥 Участники: {selected_names}\n"
        f"💸 Доля каждого: {share:.2f} ₽\n\n"
        "Если всё верно — подтвердите.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="manual_confirm"),
                InlineKeyboardButton(text="❌ Отменить", callback_data="manual_cancel"),
            ]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(ReceiptState.manual_confirmation, lambda c: c.data == "manual_confirm")
async def manual_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    chat_id = callback.message.chat.id
    amount = float(data["manual_amount"])
    category = data["manual_category"]
    payer = int(data["manual_payer"])
    selected = data["manual_selected"]

    await add_group(chat_id, callback.message.chat.title or "")
    await add_receipt(
        chat_id=chat_id,
        user_id=callback.from_user.id,
        file_id=None,
        image_path=None
    )
    receipt = await get_last_receipt(chat_id)
    receipt_id = receipt[0]

    for uid in selected:
        await link_member_to_receipt(receipt_id, uid)

    await set_receipt_payer(receipt_id, payer)
    await update_receipt_total(receipt_id, amount, "Ручной ввод", category)

    members = await get_members(chat_id)
    payer_name = next(
        (
            first_name or (f"@{username}" if username else str(uid))
            for uid, username, first_name in members
            if uid == payer
        ),
        "Неизвестный"
    )

    share = round(amount / len(selected), 2)
    for uid in selected:
        if uid == payer:
            continue
        await add_debt(
            chat_id=chat_id,
            from_user=uid,
            to_user=payer,
            receipt_id=receipt_id,
            amount=share
        )

    await callback.message.edit_text(
        "✅ <b>Покупка добавлена!</b>\n\n"
        f"💰 {amount:.2f} ₽\n"
        f"🏷 {category}\n"
        f"💳 Оплатил: {payer_name}\n"
        f"👥 Участников: {len(selected)}\n"
        f"💸 Доля: {share:.2f} ₽",
        parse_mode="HTML"
    )
    await state.clear()
    await callback.answer("Покупка сохранена!")


@dp.callback_query(ReceiptState.manual_confirmation, lambda c: c.data == "manual_cancel")
async def manual_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("❌ Добавление покупки отменено.\n\nДолги не созданы.")
    await state.clear()
    await callback.answer("Отменено")


# =========================================================
# ПОЛУЧЕНИЕ ФОТО ЧЕКА
# =========================================================

@dp.message(ReceiptState.waiting_receipt_photo, F.photo)
async def receive_receipt(message: Message, state: FSMContext):
    if message.chat.type == "private":
        await state.clear()
        await message.answer("📸 Отправляй чек в группу.")
        return

    await add_group(message.chat.id, message.chat.title or "")
    photo = message.photo[-1]
    file_name = f"{message.chat.id}_{message.from_user.id}_{photo.file_unique_id}.jpg"
    image_path = RECEIPTS_DIR / file_name

    await bot.download(photo, destination=image_path)
    await add_receipt(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        file_id=photo.file_id,
        image_path=str(image_path)
    )
    receipt = await get_last_receipt(message.chat.id)
    receipt_id = receipt[0]

    await state.update_data(receipt_id=receipt_id, payer=message.from_user.id)
    await message.answer("🤖 Анализирую чек...")

    path = await get_receipt_path(receipt_id)
    try:
        receipt_data = recognize_receipt(path)
    except Exception as e:
        print(f"❌ Ошибка распознавания чека: {e}")
        await message.answer("❌ Не удалось распознать чек.\n\nПопробуйте отправить более чёткую фотографию чека.")
        await state.clear()
        return

    store = receipt_data.get("store", "Неизвестный магазин")
    total = float(receipt_data.get("total", 0))
    items = receipt_data.get("items", [])

    if total <= 0:
        await message.answer("❌ Не удалось определить итоговую сумму чека.\n\nПопробуйте отправить более чёткую фотографию.")
        await state.clear()
        return

    members = await get_members(message.chat.id)
    if not members:
        await state.clear()
        await message.answer("❌ В группе пока нет участников.\n\nСначала нажмите «➕ Присоединиться».")
        return

    # Все товары и все участники отмечены по умолчанию.
    # Пользователь может снять галочку с конкретного товара у конкретного человека.
    item_participants = {
        str(i): [uid for uid, _, _ in members]
        for i, _ in enumerate(items)
    }

    await state.update_data(
        receipt_id=receipt_id,
        receipt_data=receipt_data,
        item_participants=item_participants,
        receipt_members=[uid for uid, _, _ in members],
        payer=message.from_user.id,
    )
    await state.set_state(ReceiptState.choosing_members)
    await show_item_participants(message, state)


async def item_participants_markup(state: FSMContext, chat_id: int):
    """Таблица-матрица: строки = товары, столбцы = участники."""
    data = await state.get_data()
    receipt_data = data.get("receipt_data", {})
    items = receipt_data.get("items", [])
    matrix = data.get("item_participants", {})
    members = await get_members(chat_id)

    builder = InlineKeyboardBuilder()

    # Заголовок таблицы.
    builder.button(text="🛒 Товар", callback_data="item_header")
    for uid, username, first_name in members:
        person = first_name or (f"@{username}" if username else str(uid))
        builder.button(text=f"👤 {person}", callback_data="item_header")

    # Каждая строка = один товар, каждая следующая кнопка = участник.
    for index, item in enumerate(items):
        name = str(item.get("name", "Неизвестный товар"))
        price = float(item.get("price", 0))

        # Не даём слишком длинному названию разъезжаться по экрану.
        if len(name) > 28:
            name = name[:27] + "…"

        builder.button(
            text=f"{name} · {price:.2f} ₽",
            callback_data=f"item_info:{index}",
        )

        selected = set(matrix.get(str(index), []))
        for uid, username, first_name in members:
            mark = "✅" if uid in selected else "⬜"
            builder.button(
                text=mark,
                callback_data=f"item_member:{index}:{uid}",
            )

    builder.button(text="➡️ Далее", callback_data="items_done")

    # Ширина каждой строки: товар + по одной ячейке на каждого участника.
    columns = max(1, len(members) + 1)
    builder.adjust(columns)
    return builder.as_markup()


async def show_item_participants(message: Message, state: FSMContext):
    data = await state.get_data()
    items = data.get("receipt_data", {}).get("items", [])
    if not items:
        await message.answer(
            "⚠️ В чеке не удалось распознать отдельные товары.\n\n"
            "Перейдём к общей сумме чека."
        )
        await state.set_state(ReceiptState.receipt_category)
        sent = await message.answer(
            "🏷 Выберите категорию покупки:",
            reply_markup=purchase_categories_keyboard("receipt_cat")
        )
        schedule_button_message_delete(sent)
        return

    # Это интерактивная матрица, поэтому её НЕ удаляем через 20 секунд.
    # Пользователь должен успеть распределить все товары.
    await message.answer(
        "🧾 <b>Кто за что платит?</b>\n\n"
        "По умолчанию все товары отмечены у всех участников.\n"
        "Если человек не покупал конкретный товар — снимите галочку под этим товаром.\n\n"
        "Например: хлеб → 2 человека, остальные товары → 3 человека.",
        reply_markup=await item_participants_markup(state, message.chat.id),
        parse_mode="HTML",
    )


@dp.callback_query(ReceiptState.choosing_members, lambda c: c.data == "item_header")
async def item_header(callback: CallbackQuery):
    await callback.answer()


@dp.callback_query(ReceiptState.choosing_members, lambda c: c.data.startswith("item_member:"))
async def toggle_item_member(callback: CallbackQuery, state: FSMContext):
    _, item_index, user_id = callback.data.split(":")
    item_index = int(item_index)
    uid = int(user_id)

    data = await state.get_data()
    items = data.get("receipt_data", {}).get("items", [])
    if item_index < 0 or item_index >= len(items):
        await callback.answer("Товар не найден", show_alert=True)
        return

    matrix = data.get("item_participants", {})
    selected = set(matrix.get(str(item_index), []))

    if uid in selected:
        selected.remove(uid)
    else:
        selected.add(uid)

    matrix[str(item_index)] = sorted(selected)
    await state.update_data(item_participants=matrix)

    # Обновляем всю матрицу в том же сообщении.
    await callback.message.edit_reply_markup(
        reply_markup=await item_participants_markup(state, callback.message.chat.id)
    )
    await callback.answer()


@dp.callback_query(ReceiptState.choosing_members, lambda c: c.data.startswith("item_info:"))
async def item_info(callback: CallbackQuery):
    item_index = int(callback.data.split(":", 1)[1])
    await callback.answer(
        "Галочки ниже относятся только к этому товару. Нажмите на имя, чтобы убрать или вернуть участника.",
        show_alert=True,
    )


@dp.callback_query(ReceiptState.choosing_members, lambda c: c.data == "items_done")
async def finish_item_participants(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    receipt_data = data.get("receipt_data", {})
    items = receipt_data.get("items", [])
    matrix = data.get("item_participants", {})

    if not items:
        await callback.answer()
        return

    # Проверяем, что для каждого товара выбран хотя бы один участник
    for index, item in enumerate(items):
        if not matrix.get(str(index), []):
            name = item.get("name", "товар")
            await callback.answer(
                f"Для товара «{name}» выберите хотя бы одного участника.",
                show_alert=True,
            )
            return

    members = await get_members(callback.message.chat.id)
    all_selected = sorted({uid for ids in matrix.values() for uid in ids})
    for uid in all_selected:
        await link_member_to_receipt(data["receipt_id"], uid)

    total = float(receipt_data.get("total", 0))
    store = receipt_data.get("store", "Неизвестный магазин")
    
    names = {
        uid: (first_name or (f"@{username}" if username else str(uid)))
        for uid, username, first_name in members
    }
    selected_names = ", ".join(names[uid] for uid in all_selected if uid in names)

    await state.update_data(
        selected=all_selected,
        names_text=selected_names,
        store=store,
        total=total,
    )
    
    # ИСПРАВЛЕНИЕ: Пропускаем шаг с выбором категории чека и сразу вызываем финальное превью
    await send_receipt_confirmation(callback.message, state, edit=True)
    await callback.answer()

# =========================================================
# КАТЕГОРИЯ ДЛЯ ЧЕКА
# =========================================================

# Изменения в файле main.py

async def send_receipt_confirmation(message: Message, state: FSMContext, *, edit: bool):
    data = await state.get_data()
    receipt_id = data["receipt_id"]
    store = data.get("store", "Неизвестный магазин")
    total = float(data.get("total", 0))
    matrix = data.get("item_participants", {})
    members = await get_members(message.chat.id)
    names = {uid: (first_name or (f"@{username}" if username else str(uid))) for uid, username, first_name in members}

    lines = [
        f"🧾 <b>Проверьте распределение чека</b>", 
        "", 
        f"🏪 {store}", 
        "", 
        "🛒 Товары и категории от ИИ:"
    ]
    
    items = data.get("receipt_data", {}).get("items", [])
    for index, item in enumerate(items):
        name = item.get("name", "Неизвестный товар")
        price = float(item.get("price", 0))
        
        # ИСПРАВЛЕНИЕ: Выводим индивидуальную категорию товара в скобках
        item_category = item.get("category", "Прочее") 
        
        selected = [names[uid] for uid in matrix.get(str(index), []) if uid in names]
        lines.append(f"• {name} — {price:.2f} ₽ <i>({item_category})</i>")
        lines.append(f"  👥 {', '.join(selected)}")

    lines += ["", f"💰 Итого: {total:.2f} ₽", "", "Если всё верно — подтвердите."]
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=f"confirm_{receipt_id}")
    builder.button(text="❌ Отменить", callback_data=f"cancel_{receipt_id}")
    builder.adjust(1)

    await state.set_state(ReceiptState.waiting_confirmation)
    text = "\n".join(lines)
    if edit:
        await message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")



@dp.callback_query(ReceiptState.receipt_category, lambda c: c.data.startswith("receipt_cat:"))
async def receipt_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    try:
        await callback.message.delete()
    except Exception:
        pass
    if category == "✏️ Своя категория":
        await state.set_state(ReceiptState.receipt_category_custom)
        await callback.message.answer("✏️ Введите свою категорию одним сообщением.\nНапример: «Дом», «Аптека» или «Питомцы».")
        await callback.answer()
        return
    await state.update_data(receipt_category=category)
    # Меню категории уже удалено, поэтому создаём сообщение заново.
    await send_receipt_confirmation(callback.message, state, edit=False)
    await callback.answer()


@dp.message(ReceiptState.receipt_category_custom)
async def receipt_custom_category(message: Message, state: FSMContext):
    category = (message.text or "").strip()
    if not category or len(category) > 50:
        await message.answer("❌ Категория должна содержать от 1 до 50 символов.")
        return
    await state.update_data(receipt_category=category.capitalize())
    await send_receipt_confirmation(message, state, edit=False)


# =========================================================
# ПОДТВЕРЖДЕНИЕ ПОКУПКИ
# =========================================================

@dp.callback_query(ReceiptState.waiting_confirmation, lambda c: c.data.startswith("confirm_"))
async def confirm_receipt(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    receipt_id = int(callback.data.split("_")[1])
    receipt_data = data.get("receipt_data")
    matrix = data.get("item_participants", {})
    payer = int(data.get("payer", callback.from_user.id))

    if not receipt_data:
        await callback.message.edit_text("❌ Данные чека потеряны.\nОтправьте чек ещё раз.")
        await state.clear()
        await callback.answer()
        return

    # Изменения в файле main.py (внутри функции confirm_receipt)

    total = float(receipt_data.get("total", 0))
    store = receipt_data.get("store", "Неизвестный магазин")
    category = data.get("receipt_category", "Прочее")
    items = receipt_data.get("items", [])

    # Сохраняем товары и их индивидуальных участников
    for index, item in enumerate(items):
        name = item.get("name", "Неизвестный товар")
        price = float(item.get("price", 0))
        item_category = item.get("category", "Прочее") # Категория от ИИ
        
        # Записываем в базу именно категорию товара
        item_id = await add_item(receipt_id, name, price, item_category) 
        
        for uid in matrix.get(str(index), []):
            await link_member_to_item(item_id, uid)

    await set_receipt_payer(receipt_id, payer)
    await update_receipt_total(receipt_id, total, store, category)

    # Для каждого товара сумма делится только между теми, у кого стоит галочка.
    debt_by_user = {}
    for index, item in enumerate(items):
        price = float(item.get("price", 0))
        participants = matrix.get(str(index), [])
        if not participants:
            continue
        base_share = round(price / len(participants), 2)
        running = 0.0
        for pos, uid in enumerate(participants):
            if pos == len(participants) - 1:
                share = round(price - running, 2)
            else:
                share = base_share
            running += share
            if uid != payer:
                debt_by_user[uid] = round(debt_by_user.get(uid, 0) + share, 2)

    for uid, amount in debt_by_user.items():
        if amount > 0:
            await add_debt(
                chat_id=callback.message.chat.id,
                from_user=uid,
                to_user=payer,
                receipt_id=receipt_id,
                amount=amount
            )

    members = await get_members(callback.message.chat.id)
    names = {uid: (first_name or (f"@{username}" if username else str(uid))) for uid, username, first_name in members}
    payer_name = names.get(payer, "Покупатель")

    debt_lines = []
    for uid, amount in sorted(debt_by_user.items(), key=lambda x: names.get(x[0], str(x[0]))):
        debt_lines.append(f"• {names.get(uid, str(uid))} — {amount:.2f} ₽")

    await callback.message.edit_text(
        f"✅ <b>Чек подтверждён!</b>\n\n"
        f"🏪 {store}\n"
        f"🏷 Категория: {category}\n"
        f"💰 Итого: {total:.2f} ₽\n"
        f"💳 Оплатил: {payer_name}\n\n"
        f"💸 Долги по товарам:\n" + ("\n".join(debt_lines) if debt_lines else "• Долгов нет"),
        parse_mode="HTML"
    )
    await state.clear()
    await callback.answer("Чек подтверждён!")


# =========================================================
# ОТМЕНА ПОКУПКИ
# =========================================================

@dp.callback_query(
    ReceiptState.waiting_confirmation,
    lambda c: c.data.startswith("cancel_")
)
async def cancel_receipt(
    callback: CallbackQuery,
    state: FSMContext
):
    await callback.message.edit_text(
        "❌ Покупка отменена.\n\n"
        "Долги не созданы."
    )

    await state.clear()

    await callback.answer(
        "Покупка отменена"
    )

@dp.callback_query(
    lambda c: c.data == "confirm_clear_members"
)
async def confirm_clear_members(
    callback: CallbackQuery
):
    await clear_members(
        callback.message.chat.id
    )

    await callback.message.edit_text(
        "🗑 Все участники группы очищены."
    )

    await callback.answer(
        "Участники удалены"
    )


@dp.callback_query(
    lambda c: c.data == "cancel_clear_members"
)
async def cancel_clear_members(
    callback: CallbackQuery
):
    await callback.message.edit_text(
        "❌ Очистка участников отменена."
    )

    await callback.answer(
        "Отменено"
    )

# =========================================================
# /receipts
# =========================================================

def receipts_list_kb(data):
    builder = InlineKeyboardBuilder()
    for receipt_id, _, created_at, *_ in data:
        builder.button(
            text=f"🧾 {created_at}",
            callback_data=f"receipt:{receipt_id}",
        )
    builder.adjust(1)
    return builder.as_markup()


@dp.message(Command("receipts"))
async def receipts(message: Message):
    data = await get_receipts(message.chat.id)

    if not data:
        await message.answer("Пока нет сохранённых чеков.")
        return

    members = await get_members(message.chat.id)
    names = {
        uid: (first_name or (f"@{username}" if username else str(uid)))
        for uid, username, first_name in members
    }

    builder = InlineKeyboardBuilder()

    for receipt in data:
        (
            receipt_id,
            _,
            created_at,
            user_id,
            username,
            first_name,
            store,
            total,
            payer_id,
        ) = receipt

        buyer_name = names.get(
            payer_id,
            first_name or (f"@{username}" if username else str(user_id))
        )

        builder.button(
            text=f"🧾 {created_at} — 👤 {buyer_name}",
            callback_data=f"receipt:{receipt_id}",
        )

    builder.adjust(1)

    sent = await message.answer(
        "🧾 Все чеки:\n\n"
        "Нажмите на чек, чтобы открыть полную информацию:",
        reply_markup=builder.as_markup(),
    )
    schedule_button_message_delete(sent)


@dp.message(lambda message: message.text == "🧾 Все чеки")
async def all_receipts_button(message: Message):
    await receipts(message)


@dp.callback_query(lambda c: c.data.startswith("receipt:"))
async def receipt_details(callback: CallbackQuery):
    receipt_id = int(callback.data.split(":", 1)[1])

    data = await get_receipts(callback.message.chat.id)
    receipt = next((r for r in data if r[0] == receipt_id), None)

    if not receipt:
        await callback.answer("Чек не найден.", show_alert=True)
        return

    (
        _,
        image_path,
        created_at,
        user_id,
        username,
        first_name,
        store,
        total,
        payer_id,
    ) = receipt

    members = await get_members(callback.message.chat.id)
    names = {
        uid: (first_name or (f"@{username}" if username else str(uid)))
        for uid, username, first_name in members
    }

    buyer_name = names.get(
        payer_id,
        first_name or (f"@{username}" if username else str(user_id))
    )

    items = await get_receipt_items(receipt_id)
    participants = await get_receipt_participants(receipt_id)

    lines = [
        f"🧾 Чек #{receipt_id}",
        f"📅 Дата: {created_at}",
        f"👤 Кто купил: {buyer_name}",
    ]

    if store:
        lines.append(f"🏪 Магазин: {store}")

    total_value = float(total or 0)
    lines.append(f"💰 Итого: {total_value:.2f} ₽")

    lines.append("")
    lines.append("🛒 Товары:")

    if items:
        for item in items:
            item_id = item[0]
            name = item[1]  # название товара
            price = item[2]
            item_category = item[3] if len(item) > 3 else None  # [NEW] категория позиции
            if item_category:  # [NEW]
                lines.append(f"• {name} — {float(price):.2f} ₽ ({item_category})")  # [NEW]
            else:  # [NEW]
                lines.append(f"• {name} — {float(price):.2f} ₽")
    else:
        lines.append("• Нет распознанных товаров.")

    lines.append("")
    lines.append("💸 Кто сколько должен:")

    # Долг считается по каждому товару отдельно — только для отмеченных участников.
    item_participants = await get_receipt_item_participants(receipt_id)
    debt_by_user = {}
    for item in items:
        item_id = item[3] if len(item) > 3 else None
        price = float(item[0] or 0)
        selected = item_participants.get(item_id, []) if item_id is not None else []
        if not selected or price <= 0:
            continue
        base_share = round(price / len(selected), 2)
        running = 0.0
        for index, uid in enumerate(selected):
            share = round(price - running, 2) if index == len(selected) - 1 else base_share
            running += share
            if payer_id and uid == payer_id:
                continue
            debt_by_user[uid] = round(debt_by_user.get(uid, 0) + share, 2)

    if debt_by_user:
        participant_names = {
            uid: (first_name or (f"@{username}" if username else str(uid)))
            for uid, username, first_name in participants
        }
        for uid, amount in sorted(debt_by_user.items(), key=lambda x: participant_names.get(x[0], str(x[0]))):
            lines.append(f"• {participant_names.get(uid, str(uid))} — {amount:.2f} ₽")
    elif participants:
        lines.append("• Долгов нет (все выбранные участники — покупатель).")
    else:
        lines.append("• Участники не указаны.")

    # Фото чека при просмотре списка чеков не читаем и не отправляем.
    # Показываем только сохранённые данные: товары, цены и расчёт долгов.
    await callback.message.answer("\n".join(lines))

    await callback.answer()

# =========================================================
# МЕНЮ "ЧЕКИ"
# =========================================================

@dp.message(lambda message: message.text == "🧾 История покупок")
async def checks_menu(message: Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="🧾 Все чеки", callback_data="checks:all")
    builder.button(text="🧾 Мои чеки", callback_data="checks:mine")
    builder.button(text="⬅️ Назад", callback_data="checks:back")
    builder.adjust(1)

    sent = await message.answer(
        "🧾 <b>История покупок</b>\n\nВыберите, что открыть:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    schedule_button_message_delete(sent)


@dp.callback_query(lambda c: c.data == "checks:all")
async def checks_all(callback: CallbackQuery):
    await callback.message.delete()
    await receipts(callback.message)
    await callback.answer()


@dp.callback_query(lambda c: c.data == "checks:mine")
async def checks_mine(callback: CallbackQuery):
    await callback.message.delete()
    await my_receipts_button(callback.message, user_id=callback.from_user.id)
    await callback.answer()


@dp.callback_query(lambda c: c.data == "checks:back")
async def checks_back(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()


# =========================================================
# КНОПКА "МОИ ЧЕКИ"
# =========================================================

@dp.message(lambda message: message.text == "🧾 Мои чеки")
async def my_receipts_button(message: Message, user_id: int | None = None):
    if message.chat.type == "private":
        await message.answer(
            "❌ Эту функцию нужно использовать в группе."
        )
        return

    # При открытии через inline-меню message.from_user — это бот,
    # поэтому обязательно используем реального пользователя callback.
    actual_user_id = user_id if user_id is not None else message.from_user.id
    data = await get_my_receipts(
        message.chat.id,
        actual_user_id
    )

    if not data:
        await message.answer(
            "🧾 В вашей истории пока нет покупок.\n\n"
            "Добавьте покупку вручную или загрузите чек."
        )
        return

    builder = InlineKeyboardBuilder()

    for receipt_id, date, store, total, payer_id, source in data:
        source_icon = "✍️" if source == "manual" else "📸"
        store_name = store or "Покупка"
        total_text = f"{float(total):.2f} ₽" if total is not None else "сумма не указана"
        builder.button(
            text=f"{source_icon} {date} — {store_name} — {total_text}",
            callback_data=f"receipt:{receipt_id}",
        )

    builder.adjust(1)

    sent = await message.answer(
        "🧾 <b>Моя история покупок</b>\n\n"
        "✍️ — ручной ввод, 📸 — чек.\n"
        "Нажмите на покупку, чтобы открыть подробности.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    schedule_button_message_delete(sent)


# =========================================================
# КНОПКА / КОМАНДА "МОИ ДОЛГИ"
# =========================================================

async def get_raw_debts(chat_id: int):
    """Возвращает все долги без какой-либо оптимизации."""
    raw_debts = await get_all_debts(chat_id)
    return [(from_id, to_id, amount) for from_id, to_id, amount, _ in raw_debts]

async def calculate_net_debts(chat_id: int):
    """Сводит встречные долги (взаимозачёт), но не оптимизирует количество транзакций."""
    raw_debts = await get_raw_debts(chat_id)
    
    # Баланс: положительный = человеку должны, отрицательный = человек должен.
    balances = {}
    for from_id, to_id, amount in raw_debts:
        amount = float(amount)
        if amount <= 0:
            continue
        balances[from_id] = balances.get(from_id, 0.0) - amount
        balances[to_id] = balances.get(to_id, 0.0) + amount
    
    # Возвращаем только тех, у кого ненулевой баланс
    result = []
    for uid, balance in balances.items():
        if abs(balance) > 0.005:
            result.append((uid, balance))
    
    return result

def optimize_debts(net_balances):
    """
    Минимизирует количество транзакций для закрытия всех долгов.
    net_balances: список (user_id, balance) — баланс после взаимозачёта.
    Возвращает оптимизированный список (from_id, to_id, amount).
    """
    # Разделяем на должников (отрицательный баланс) и кредиторов (положительный)
    debtors = [(uid, -balance) for uid, balance in net_balances if balance < -0.005]
    creditors = [(uid, balance) for uid, balance in net_balances if balance > 0.005]
    
    # Сортируем по убыванию суммы для жадного алгоритма
    debtors.sort(key=lambda x: x[1], reverse=True)
    creditors.sort(key=lambda x: x[1], reverse=True)
    
    optimized = []
    i, j = 0, 0
    
    while i < len(debtors) and j < len(creditors):
        debtor_id, debt_amount = debtors[i]
        creditor_id, credit_amount = creditors[j]
        
        transfer = min(debt_amount, credit_amount)
        if transfer > 0.005:
            optimized.append((debtor_id, creditor_id, round(transfer, 2)))
        
        debtors[i] = (debtor_id, debt_amount - transfer)
        creditors[j] = (creditor_id, credit_amount - transfer)
        
        if debtors[i][1] <= 0.005:
            i += 1
        if creditors[j][1] <= 0.005:
            j += 1
    
    return optimized

'''
async def calculate_net_debts(chat_id: int):
    """Сводит встречные долги и возвращает только конечные обязательства."""
    raw_debts = await get_debts(chat_id)

    # Баланс: положительный = человеку должны, отрицательный = человек должен.
    balances = {}
    for from_id, to_id, amount in raw_debts:
        amount = float(amount)
        if amount <= 0:
            continue
        balances[from_id] = balances.get(from_id, 0.0) - amount
        balances[to_id] = balances.get(to_id, 0.0) + amount

    debtors = [[uid, -balance] for uid, balance in balances.items() if balance < -0.005]
    creditors = [[uid, balance] for uid, balance in balances.items() if balance > 0.005]

    result = []
    di = ci = 0
    while di < len(debtors) and ci < len(creditors):
        debtor_id, debt = debtors[di]
        creditor_id, credit = creditors[ci]
        amount = min(debt, credit)

        if amount > 0.005:
            result.append((debtor_id, creditor_id, round(amount, 2)))

        debtors[di][1] -= amount
        creditors[ci][1] -= amount
        if debtors[di][1] <= 0.005:
            di += 1
        if creditors[ci][1] <= 0.005:
            ci += 1

    return result

def optimize_debts(net_debts):
    """
    Минимизирует количество транзакций для закрытия всех долгов.
    net_debts: список (from_id, to_id, amount) — уже после взаимозачёта.
    Возвращает оптимизированный список (from_id, to_id, amount).
    """
    # Считаем баланс каждого участника
    balances = {}
    for from_id, to_id, amount in net_debts:
        amount = float(amount)
        if amount <= 0:
            continue
        balances[from_id] = balances.get(from_id, 0.0) - amount
        balances[to_id] = balances.get(to_id, 0.0) + amount
    
    # Разделяем на должников (отрицательный баланс) и кредиторов (положительный)
    debtors = [(uid, -balance) for uid, balance in balances.items() if balance < -0.005]
    creditors = [(uid, balance) for uid, balance in balances.items() if balance > 0.005]
    
    # Сортируем по убыванию суммы для жадного алгоритма
    debtors.sort(key=lambda x: x[1], reverse=True)
    creditors.sort(key=lambda x: x[1], reverse=True)
    
    optimized = []
    i, j = 0, 0
    
    while i < len(debtors) and j < len(creditors):
        debtor_id, debt_amount = debtors[i]
        creditor_id, credit_amount = creditors[j]
        
        transfer = min(debt_amount, credit_amount)
        if transfer > 0.005:
            optimized.append((debtor_id, creditor_id, round(transfer, 2)))
        
        debtors[i] = (debtor_id, debt_amount - transfer)
        creditors[j] = (creditor_id, credit_amount - transfer)
        
        if debtors[i][1] <= 0.005:
            i += 1
        if creditors[j][1] <= 0.005:
            j += 1
    
    return optimized
'''
@dp.message(Command("my_debts"))
async def my_debts_command(message: Message):
    await show_my_debts(message)

async def show_my_debts(message: Message, user_id: int | None = None):
    if message.chat.type == "private":
        await message.answer("❌ Эту функцию нужно использовать в группе.")
        return
    
    actual_user_id = user_id if user_id is not None else message.from_user.id
    
    # calculate_net_debts теперь возвращает списки балансов: [(user_id, balance), ...]
    net_balances = await calculate_net_debts(message.chat.id)
    
    # Ищем баланс текущего пользователя
    my_balance = next((balance for uid, balance in net_balances if uid == actual_user_id), 0.0)
    
    # Если баланс положительный или нулевой — пользователь никому не должен
    if my_balance >= -0.005:
        await message.answer("✅ После взаимозачёта вы никому ничего не должны.")
        return
    
    # Отрицательный баланс = долг
    debt_amount = abs(my_balance)
    
    members = await get_members(message.chat.id)
    names = {uid: (first_name or (f"@{username}" if username else str(uid))) 
             for uid, username, first_name in members}
    
    lines = ["💳 <b>Мои долги</b>", ""]
    lines.append(f"• Общая сумма долга: <b>{debt_amount:.2f} ₽</b>")
    lines.append("")
    lines.append(f"💰 <b>Всего: {debt_amount:.2f} ₽</b>")
    
    await message.answer("\n".join(lines), parse_mode="HTML")



# =========================================================
# /debts
# =========================================================

@dp.message(Command("debts"))
async def show_debts(message: Message):
    # Показываем балансы после взаимозачёта (кто кому должен в итоге)
    net_balances = await calculate_net_debts(message.chat.id)
    
    if not net_balances:
        await message.answer("✅ После взаимозачёта никто никому ничего не должен.")
        return
    
    members = await get_members(message.chat.id)
    names = {uid: name for uid, _, name in members}
    
    lines = ["💸 <b>Балансы после взаимозачёта:</b>\n"]
    
    for uid, balance in net_balances:
        name = names.get(uid, "Неизвестный")
        if balance > 0:
            lines.append(f"• {name}: <b>+{balance:.2f} ₽</b> (ему должны)")
        else:
            lines.append(f"• {name}: <b>{balance:.2f} ₽</b> (он должен)")
    
    lines.append("\n💡 Нажмите «🔄 Оптимизировать долги» для минимальной схемы переводов.")
    
    await message.answer("\n".join(lines), parse_mode="HTML")



# =========================================================
# КНОПКА "ДОЛГИ" — ВЛОЖЕННОЕ МЕНЮ
# =========================================================

def debts_menu_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💸 Все долги",
                    callback_data="debts_all"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Мои долги",  # Исправлено (символ вернулся)
                    callback_data="debts_mine"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Оптимизировать долги",
                    callback_data="debts_optimize"
                )
            ],
            [
                # НОВАЯ КНОПКА
                InlineKeyboardButton(
                    text="🔔 Напомнить о долгах",
                    callback_data="debts_remind_select"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад",  # Исправлено (символ вернулся)
                    callback_data="debts_back"
                )
            ],
        ]
    )


@dp.message(lambda message: message.text == "💸 Долги")
async def debts_button(message: Message):
    sent = await message.answer(
        "💸 <b>Долги</b>\n\nВыберите, что хотите посмотреть:",
        reply_markup=debts_menu_keyboard(),
        parse_mode="HTML"
    )
    schedule_button_message_delete(sent)



async def delete_debts_menu(callback: CallbackQuery):
    # После выбора пункта убираем промежуточное сообщение с меню,
    # чтобы в чате не оставались старые кнопки.
    try:
        await callback.message.delete()
    except Exception:
        # Если Telegram не разрешил удалить сообщение, хотя бы убираем кнопки.
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

@dp.callback_query(lambda c: c.data == "debts_all")
async def debts_all_callback(callback: CallbackQuery):
    await delete_debts_menu(callback)
    await callback.answer()
    
    # Показываем ВСЕ долги без оптимизации
    raw_debts = await get_raw_debts(callback.message.chat.id)
    
    if not raw_debts:
        await callback.message.answer("✅ Долгов нет.")
        return
    
    members = await get_members(callback.message.chat.id)
    names = {uid: name for uid, _, name in members}
    
    lines = ["📋 <b>Все долги (без оптимизации):</b>\n"]
    total = 0.0
    
    for from_id, to_id, amount in raw_debts:
        from_name = names.get(from_id, "Неизвестный")
        to_name = names.get(to_id, "Неизвестный")
        lines.append(f"• {from_name} → {to_name}: <b>{amount:.2f} ₽</b>")
        total += amount
    
    lines.append(f"\n💰 Всего переводов: <b>{len(raw_debts)}</b>")
    lines.append(f"💰 Общий оборот: <b>{total:.2f} ₽</b>")
    
    await callback.message.answer("\n".join(lines), parse_mode="HTML")

@dp.callback_query(lambda c: c.data == "debts_mine")
async def debts_mine_callback(callback: CallbackQuery):
    await delete_debts_menu(callback)
    await callback.answer()
    await show_my_debts(callback.message, callback.from_user.id)

@dp.callback_query(lambda c: c.data == "debts_optimize")
async def debts_optimize_callback(callback: CallbackQuery):
    await delete_debts_menu(callback)
    await callback.answer()
    
    # Сначала делаем взаимозачёт
    net_balances = await calculate_net_debts(callback.message.chat.id)
    
    if not net_balances:
        await callback.message.answer("✅ После взаимозачёта никто никому ничего не должен.")
        return
    
    # Затем оптимизируем количество транзакций
    optimized = optimize_debts(net_balances)
    
    if not optimized:
        await callback.message.answer("✅ После оптимизации переводы не нужны.")
        return
    
    members = await get_members(callback.message.chat.id)
    names = {uid: name for uid, _, name in members}
    
    # Считаем статистику
    raw_debts = await get_raw_debts(callback.message.chat.id)
    original_count = len(raw_debts)
    original_total = sum(amount for _, _, amount in raw_debts)
    optimized_total = sum(amount for _, _, amount in optimized)
    
    lines = [
        "🔄 <b>Оптимизация долгов</b>\n",
        f"📊 Было: <b>{original_count}</b> переводов на <b>{original_total:.2f} ₽</b>",
        f"✅ Стало: <b>{len(optimized)}</b> переводов на <b>{optimized_total:.2f} ₽</b>\n",
        "<b>Оптимальная схема расчётов:</b>\n"
    ]
    
    for from_id, to_id, amount in optimized:
        from_name = names.get(from_id, "Неизвестный")
        to_name = names.get(to_id, "Неизвестный")
        lines.append(f"• {from_name} → {to_name}: <b>{amount:.2f} ₽</b>")
    
    lines.append(f"\n💡 Экономия: <b>{original_count - len(optimized)}</b> переводов")
    lines.append(f"💡 Меньше оборот на: <b>{original_total - optimized_total:.2f} ₽</b>")
    
    await callback.message.answer("\n".join(lines), parse_mode="HTML")


@dp.callback_query(lambda c: c.data == "debts_back")
async def debts_back_callback(callback: CallbackQuery):
    await delete_debts_menu(callback)
    await callback.answer("Назад")

@dp.callback_query(lambda c: c.data == "debts_optimize")
async def debts_optimize_callback(callback: CallbackQuery):
    await delete_debts_menu(callback)
    await callback.answer()
    
    # Получаем текущие долги после взаимозачёта
    net_debts = await calculate_net_debts(callback.message.chat.id)
    
    if not net_debts:
        await callback.message.answer(
            "✅ После взаимозачёта никто никому ничего не должен."
        )
        return
    
    # Оптимизируем
    optimized = optimize_debts(net_debts)
    
    members = await get_members(callback.message.chat.id)
    names = {uid: name for uid, _, name in members}
    
    # Считаем экономию
    original_count = len(net_debts)
    optimized_count = len(optimized)
    original_total = sum(amount for _, _, amount in net_debts)
    optimized_total = sum(amount for _, _, amount in optimized)
    
    lines = [
        "🔄 <b>Оптимизация долгов</b>\n",
        f"📊 Было: <b>{original_count}</b> переводов на <b>{original_total:.2f} ₽</b>",
        f"✅ Стало: <b>{optimized_count}</b> переводов на <b>{optimized_total:.2f} ₽</b>\n",
        "<b>Оптимальная схема расчётов:</b>\n"
    ]
    
    for from_id, to_id, amount in optimized:
        from_name = names.get(from_id, "Неизвестный")
        to_name = names.get(to_id, "Неизвестный")
        lines.append(f"• {from_name} → {to_name}: <b>{amount:.2f} ₽</b>")
    
    lines.append(f"\n💡 Экономия: <b>{original_count - optimized_count}</b> переводов")
    
    await callback.message.answer("\n".join(lines), parse_mode="HTML")

# =========================================================
# [NEW] КРУГОВАЯ ДИАГРАММА РАСХОДОВ ПО КАТЕГОРИЯМ
# =========================================================

def _first_existing_font(paths):  # [NEW] берём первый найденный файл шрифта
    for path in paths:  # [NEW]
        if os.path.exists(path):  # [NEW]
            return font_manager.FontProperties(fname=path)  # [NEW]
    return font_manager.FontProperties()  # [NEW]


def _cyrillic_fonts():  # [CHG] обычный и жирный шрифт для заголовков и подписей
    regular = _first_existing_font([  # [CHG]
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\tahoma.ttf",
    ])
    bold = _first_existing_font([  # [NEW] жирный вариант для заголовка и суммы
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\calibrib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\tahomabd.ttf",
    ])
    return regular, bold  # [NEW]


def build_category_pie_chart(rows):  # [CHG] вертикальная картинка: на телефоне текст не сжимается
    labels = [str(row[0]) for row in rows]  # [NEW] названия категорий
    values = [float(row[1]) for row in rows]  # [NEW] суммы по категориям
    total = sum(values)  # [NEW] итог в центре кольца
    font, font_bold = _cyrillic_fonts()  # [CHG]

    bg = "#F6F3EE"  # [NEW] тёплый фон вместо резкого белого
    ink = "#1F2A37"  # [NEW] цвет заголовков
    muted = "#5C6B7A"  # [NEW] цвет второстепенного текста
    palette = [  # [CHG] спокойная палитра, без кислотных цветов tab20
        "#5B8DEF",
        "#3DBE9A",
        "#F3B23C",
        "#7B6CFF",
        "#F07A5A",
        "#4AA8D8",
        "#C26BD4",
        "#6B8F71",
        "#E08AB0",
        "#8A9BB5",
    ]
    colors = [palette[i % len(palette)] for i in range(len(values))]  # [CHG]

    legend_rows = max(len(values), 3)  # [NEW] высота картинки растёт вместе с легендой
    fig_h = 9.2 + legend_rows * 0.55  # [NEW]
    fig, ax = plt.subplots(figsize=(8.2, fig_h), facecolor=bg)  # [CHG] узкий портрет — крупнее на экране телефона
    ax.set_facecolor(bg)  # [NEW]

    def slice_label(pct):  # [NEW] мелкие сектора не подписываем, чтобы не наслаивался текст
        return f"{pct:.0f}%" if pct >= 5 else ""  # [CHG] порог чуть выше: крупные цифры читаются лучше

    wedges, _, autotexts = ax.pie(  # [CHG] кольцо с зазорами между секторами
        values,
        autopct=slice_label,
        startangle=90,
        pctdistance=0.78,
        colors=colors,
        radius=1.18,  # [NEW] круг крупнее относительно холста
        wedgeprops={
            "width": 0.50,
            "edgecolor": bg,
            "linewidth": 4.0,
            "antialiased": True,
        },
    )

    for autotext in autotexts:  # [CHG] проценты крупно, чтобы читались после сжатия Telegram
        autotext.set_fontproperties(font_bold)
        autotext.set_fontsize(25)
        autotext.set_color("#FFFFFF")

    ax.text(  # [NEW] подпись в центре кольца
        0,
        0.14,
        "Итого",
        ha="center",
        va="center",
        fontproperties=font,
        fontsize=22,
        color=muted,
    )
    ax.text(  # [NEW] сумма в центре
        0,
        -0.14,
        f"{total:,.0f} ₽".replace(",", " "),
        ha="center",
        va="center",
        fontproperties=font_bold,
        fontsize=28,
        color=ink,
    )

    ax.set_title("")  # [CHG] заголовок рисуем сами, чтобы разделить жирный и обычный текст
    ax.text(  # [CHG]
        0.5,
        1.28,
        "Расходы по категориям",
        transform=ax.transAxes,
        fontproperties=font_bold,
        fontsize=30,
        color=ink,
        ha="center",
        va="bottom",
    )
    ax.text(  # [NEW]
        0.5,
        1.14,
        "за последние 7 дней",
        transform=ax.transAxes,
        fontproperties=font,
        fontsize=24,
        color=muted,
        ha="center",
        va="bottom",
    )

    legend_labels = [  # [CHG] одна строка крупным шрифтом — удобнее на телефоне
        f"  {label}   {value:,.0f} ₽   {value / total * 100:.0f}%".replace(",", " ")
        for label, value in zip(labels, values)
    ]
    legend = ax.legend(  # [CHG] легенда под кругом, а не сбоку — не мельчает на узком экране
        wedges,
        legend_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.08),
        frameon=False,
        handlelength=1.4,
        handleheight=1.4,
        markerscale=1.6,
        borderaxespad=0.0,
        labelspacing=1.15,
        fontsize=22,
    )
    for text in legend.get_texts():  # [NEW] подписи легенды крупнее и темнее
        text.set_fontproperties(font)
        text.set_fontsize(22)
        text.set_color(ink)
        text.set_va("center")

    ax.set_aspect("equal")  # [NEW] круг без сжатия
    fig.subplots_adjust(left=0.06, right=0.94, top=0.78, bottom=0.22)  # [CHG] запас сверху и снизу под крупный текст

    buffer = BytesIO()  # [NEW] сохраняем картинку в память
    fig.savefig(  # [CHG] высокий dpi: Telegram сожмёт картинку, буквы останутся чёткими
        buffer,
        format="png",
        dpi=180,
        facecolor=bg,
        bbox_inches="tight",
        pad_inches=0.45,
    )
    plt.close(fig)  # [NEW] освобождаем память matplotlib
    buffer.seek(0)  # [NEW] курсор в начало буфера перед отправкой
    return buffer.getvalue()  # [NEW] байты готовой картинки


@dp.message(Command("categories"))  
async def show_category_chart(message: Message):
    if message.chat.type == "private":  
        await message.answer("❌ Эту команду нужно использовать в группе.")
        return

    rows = await get_category_totals_last_week(message.chat.id)  

    if not rows:  
        await message.answer(
            "📊 За последнюю неделю ещё нет расходов по категориям.\n\n"  
            "Добавьте покупку кнопкой «✍️ Добавить покупку»."
        )
        return

    total = sum(float(amount) for _, amount in rows)  
    lines = ["📊 Расходы по категориям за 7 дней:\n"]  
    for category, amount in rows:
        share = (float(amount) / total) * 100 if total else 0
        lines.append(f"• {category}: {float(amount):.2f} ₽ ({share:.1f}%)")
    lines.append(f"\n💰 Итого: {total:.2f} ₽")

    caption = "\n".join(lines)  
    photo_bytes = build_category_pie_chart(rows)  
    photo = BufferedInputFile(photo_bytes, filename="categories.png")  

    # [CHG] Добавляем две компактные кнопки в один ряд, чтобы они идеально смотрелись на мобильных
    builder = InlineKeyboardBuilder()
    builder.button(text="💡 Совет ИИ", callback_data="get_ai_budget_advice")
    builder.button(text="📊 Прогноз трат", callback_data="get_budget_forecast")
    builder.adjust(1) # Цифра 2 распределит их горизонтально в один ряд
    inline_kb = builder.as_markup()



    if len(caption) > 1000:  
        await message.answer_photo(photo=photo, caption="📊 Расходы по категориям за 7 дней")  
        await message.answer(caption, reply_markup=inline_kb) # прикрепляем к тексту
    else:
        await message.answer_photo(photo=photo, caption=caption, reply_markup=inline_kb) # прикрепляем к фото


@dp.message(lambda message: message.text == "📊 Категории")  # [NEW] кнопка панели, как у «Все долги»
async def categories_button(message: Message):
    await show_category_chart(message)  # [NEW] та же логика, что у slash-команды


# =========================================================
# /clear_debts
# =========================================================

@dp.message(Command("clear_debts"))
async def clear_all_debts(message: Message):
    if message.chat.type == "private":
        await message.answer(
            "❌ Эту команду нужно использовать "
            "в группе."
        )
        return

    await clear_debts(
        message.chat.id
    )

    await message.answer(
        "🧹 Все долги этой группы очищены."
    )


# =========================================================
# КНОПКА "ОЧИСТИТЬ" — ВЛОЖЕННОЕ МЕНЮ
# =========================================================

def clear_menu_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧹 Очистить долги",
                    callback_data="clear_menu_debts"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Очистить участников",
                    callback_data="clear_menu_members"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧾 Очистить список трат",
                    callback_data="clear_menu_expenses"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="clear_menu_back"
                )
            ],
        ]
    )


@dp.message(lambda message: message.text == "🧹 Очистить")
async def clear_menu_button(message: Message):
    if message.chat.type == "private":
        await message.answer("❌ Эту функцию нужно использовать в группе.")
        return

    sent = await message.answer(
        "🧹 <b>Очистить</b>\n\nВыберите, что хотите удалить:",
        reply_markup=clear_menu_keyboard(),
        parse_mode="HTML"
    )
    schedule_button_message_delete(sent)


async def delete_clear_menu(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


@dp.callback_query(lambda c: c.data == "clear_menu_back")
async def clear_menu_back_callback(callback: CallbackQuery):
    await delete_clear_menu(callback)
    await callback.answer("Назад")


@dp.callback_query(lambda c: c.data == "clear_menu_debts")
async def clear_menu_debts_callback(callback: CallbackQuery):
    await delete_clear_menu(callback)
    await callback.answer()
    await send_clear_debts_confirmation(callback.message)


@dp.callback_query(lambda c: c.data == "clear_menu_members")
async def clear_menu_members_callback(callback: CallbackQuery):
    await delete_clear_menu(callback)
    await callback.answer()
    await send_clear_members_confirmation(callback.message)


@dp.callback_query(lambda c: c.data == "clear_menu_expenses")
async def clear_menu_expenses_callback(callback: CallbackQuery):
    await delete_clear_menu(callback)
    await callback.answer()
    await send_clear_expenses_confirmation(callback.message)


async def send_clear_debts_confirmation(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, очистить", callback_data="confirm_clear_debts"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_clear_debts")
            ]
        ]
    )
    sent = await message.answer(
        "⚠️ Вы уверены, что хотите удалить ВСЕ долги в этой группе?\n\n"
        "Это действие нельзя отменить.",
        reply_markup=keyboard
    )
    schedule_button_message_delete(sent)


async def send_clear_members_confirmation(message: Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Да, очистить", callback_data="confirm_clear_members")
    builder.button(text="❌ Отмена", callback_data="cancel_clear_members")
    builder.adjust(1)
    sent = await message.answer(
        "⚠️ <b>Очистить всех участников?</b>\n\n"
        "Все участники этой группы будут удалены из списка.\n\n"
        "Чеки и долги при этом не удаляются.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    schedule_button_message_delete(sent)


async def send_clear_expenses_confirmation(message: Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Да, очистить", callback_data="confirm_clear_expenses")
    builder.button(text="❌ Отмена", callback_data="cancel_clear_expenses")
    builder.adjust(1)
    sent = await message.answer(
        "⚠️ <b>Очистить список трат?</b>\n\n"
        "Будут удалены все чеки и ручные покупки этой группы.\n\n"
        "Долги и участники группы останутся.\n\n"
        "Это действие нельзя отменить.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    schedule_button_message_delete(sent)


# Старые текстовые кнопки оставляем для совместимости, если они где-то ещё используются.



@dp.callback_query(lambda callback: callback.data == "confirm_clear_debts")
async def confirm_clear_debts(callback: CallbackQuery):
    await clear_debts(callback.message.chat.id)

    await callback.message.delete()
    await callback.answer("Долги очищены")


@dp.callback_query(lambda callback: callback.data == "cancel_clear_debts")
async def cancel_clear_debts(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer("Отменено")

# =========================================================
# =========================================================
# ПОДТВЕРЖДЕНИЯ ОЧИСТКИ
# =========================================================

@dp.callback_query(lambda c: c.data == "confirm_clear_members")
async def confirm_clear_members(callback: CallbackQuery):
    await clear_members(callback.message.chat.id)
    await callback.message.delete()
    await callback.answer("Участники удалены")


@dp.callback_query(lambda c: c.data == "cancel_clear_members")
async def cancel_clear_members(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer("Отменено")


@dp.callback_query(lambda c: c.data == "confirm_clear_expenses")
async def confirm_clear_expenses(callback: CallbackQuery):
    await clear_expenses(callback.message.chat.id)
    await callback.message.delete()
    await callback.answer("Траты удалены")


@dp.callback_query(lambda c: c.data == "cancel_clear_expenses")
async def cancel_clear_expenses(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer("Отменено")

@dp.callback_query(lambda c: c.data == "get_ai_budget_advice")
async def handle_ai_advice_request(callback: CallbackQuery, state: FSMContext):
    # Проверяем, не запущена ли уже генерация, чтобы избежать спама нажатиями
    current_state = await state.get_state()
    if current_state == ReceiptState.waiting_ai_advice:
        await callback.answer("⏳ ИИ уже анализирует ваши расходы, пожалуйста, подождите...", show_alert=True)
        return
        
    await state.set_state(ReceiptState.waiting_ai_advice)
    await callback.answer("Анализирую расходы...")
    
    # Отправляем промежуточный статус
    status_msg = await callback.message.answer("🤖 <i>ИИ изучает структуру ваших трат за неделю и подбирает рекомендации...</i>", parse_mode="HTML")
    
    try:
        # Получаем свежие данные расходов для промпта
        rows = await get_category_totals_last_week(callback.message.chat.id)
        if not rows:
            await status_msg.edit_text("❌ Данные о расходах внезапно изменились или пропали.")
            await state.clear()
            return
            
        # Вызываем функцию генерации совета
        advice_text = await asyncio.to_thread(get_ai_savings_advice, rows)
        
        # Выводим готовый совет
        await status_msg.edit_text(f"💡 <b>Совет по оптимизации от ИИ:</b>\n\n{advice_text}", parse_mode="HTML")
        
        # Убираем кнопку «Получить совет» под графиком, чтобы меню выглядело чистым
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
            
    except Exception as e:
        print(f"❌ Ошибка генерации ИИ-совета: {e}")
        await status_msg.edit_text("❌ К сожалению, не удалось связаться с ИИ-советником. Попробуйте позже.")
    finally:
        await state.clear() # В любом случае снимаем блокировку состояния

@dp.callback_query(lambda c: c.data == "get_budget_forecast")
async def handle_budget_forecast_request(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Рассчитываю прогноз...")
    
    # Расчет дней средствами Python (для точной математики)
    now = datetime.datetime.now()
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    current_day = now.day
    days_left = days_in_month - current_day + 1 # включая сегодняшний день
    
    # Берем данные трат из БД
    current_week_total, current_month_total, last_month_total = await get_budget_forecast_data(callback.message.chat.id)
    
    # Расчет темпа на основе последних 7 дней (или сколько прошло с начала месяца, если меньше)
    days_for_pace = min(current_day, 7)
    if days_for_pace == 0: days_for_pace = 1
    
    avg_per_day = round(current_week_total / days_for_pace, 2)
    
    # Прогноз: сколько уже потрачено + (среднее в день * сколько дней осталось)
    forecast_remaining = avg_per_day * (days_in_month - current_day)
    expected_total = round(current_month_total + max(0, forecast_remaining), 2)
    
    # Разница с прошлым месяцем в %
    if last_month_total > 0:
        percent_diff = round(((expected_total - last_month_total) / last_month_total) * 100, 1)
    else:
        percent_diff = 0.0
        
    # Формируем красивое сообщение как на скриншоте
    lines = [
        "📊 <b>Прогноз расходов группы</b>\n",
        f"💳 Потрачено в этом месяце: <b>{current_month_total:.2f} ₽</b>",
        f"📉 Средний расход в день: <b>{avg_per_day:.2f} ₽</b>\n",
        "<i>Если текущий темп трат сохранится:</i>",
        f"≈ <b>{expected_total:.2f} ₽</b> ожидается до конца месяца\n"
    ]

    # Блок сравнения с прошлым месяцем — только если есть данные
    if last_month_total > 0:
        if percent_diff > 0:
            lines.append(f"⚠️ Это на <b>{percent_diff}% больше</b> трат прошлого месяца ({last_month_total:.2f} ₽).")
        elif percent_diff < 0:
            lines.append(f"✅ Это на <b>{abs(percent_diff)}% меньше</b> трат прошлого месяца ({last_month_total:.2f} ₽).")
        else:
            lines.append("Идете ровно по графику прошлого месяца.")
    else:
        lines.append("ℹ️ <i>Данных за прошлый месяц пока нет.</i>")

        # ИИ-комментарий добавляем ТОЛЬКО если есть данные за прошлый месяц.
    # Без них ИИ нечего сравнивать, и комментарий будет лишним.
    if last_month_total > 0:
        lines.append("\n🤖 <b>Комментарий ИИ:</b>")

        # Отправляем временный статус, так как идём в сеть к ИИ за текстовым вердиктом
        status_msg = await callback.message.answer(
            "🤖 <i>Секунду, ИИ оценивает ваш темп трат...</i>",
            parse_mode="HTML"
        )

        try:
            # Запрашиваем у ИИ только текстовую оценку готовых цифр
            ai_verdict = await asyncio.to_thread(
                get_ai_forecast_verdict,
                current_month_total,
                expected_total,
                percent_diff,
            )
            lines.append(ai_verdict)

            # Выводим итоговый красивый результат
            await status_msg.edit_text("\n".join(lines), parse_mode="HTML")

            # Подчищаем inline-кнопки под графиком категорий
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        except Exception as e:
            print(f"❌ Ошибка получения вердикта ИИ: {e}")
            lines.append("<i>Не удалось загрузить ИИ-оценку, но ваши цифры выше математически точны!</i>")
            await status_msg.edit_text("\n".join(lines), parse_mode="HTML")
    else:
        # Данных за прошлый месяц нет — просто показываем цифры без ИИ
        await callback.message.answer(
            "\n".join(lines),
            parse_mode="HTML"
        )
        # Подчищаем inline-кнопки под графиком категорий
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

# Добавить в файл main.py

@dp.callback_query(lambda c: c.data == "debts_remind_select")
async def debts_remind_select_callback(callback: CallbackQuery):
    await delete_debts_menu(callback)
    await callback.answer()

    my_id = callback.from_user.id
    chat_id = callback.message.chat.id

    # Получаем балансы после взаимозачета
    net_balances = await calculate_net_debts(chat_id)
    
    # Получаем имена участников группы для отображения на кнопках
    members = await get_members(chat_id)
    names = {uid: (first_name or (f"@{username}" if username else str(uid))) 
             for uid, username, first_name in members}

    # Считаем, кто именно и сколько должен текущему пользователю (минимизированный жадный расчет)
    optimized = optimize_debts(net_balances)
    
    # Фильтруем транзакции, где получателем (кредитором) является текущий пользователь
    my_debtors = [tx for tx in optimized if tx[1] == my_id]

    if not my_debtors:
        await callback.message.answer(
            "🔔 <b>Напоминание о долгах</b>\n\n"
            "После оптимизации расчетов сейчас никто лично вам ничего не должен! 🎉",
            parse_mode="HTML"
        )
        return

    builder = InlineKeyboardBuilder()
    lines = ["🔔 <b>Кому вы хотите напомнить о долге?</b>\n"]
    
    for debtor_id, _, amount in my_debtors:
        debtor_name = names.get(debtor_id, "Неизвестный")
        lines.append(f"• {debtor_name} должен вам <b>{amount:.2f} ₽</b>")
        
        # Кнопка для отправки уведомления конкретному человеку
        builder.button(
            text=f"💬 Напомнить {debtor_name}",
            callback_data=f"remind_send:{debtor_id}:{amount:.2f}"
        )
        
    builder.adjust(1)
    
    await callback.message.answer(
        "\n".join(lines), 
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@dp.callback_query(lambda c: c.data.startswith("remind_send:"))
async def debts_remind_send_callback(callback: CallbackQuery):
    await callback.answer()
    
    # Парсим данные из callback_data
    _, debtor_id_str, amount_str = callback.data.split(":")
    debtor_id = int(debtor_id_str)
    amount = float(amount_str)
    
    creditor_name = callback.from_user.first_name or f"@{callback.from_user.username}"
    
    try:
        # Пытаемся отправить сообщение должнику в личные сообщения
        await bot.send_message(
            chat_id=debtor_id,
            text=f"🔔 <b>Напоминание о долге!</b>\n\n"
                 f"Привет! Напоминаем, что в группе расходов «<b>{callback.message.chat.title}</b>» "
                 f"твой оптимизированный долг перед пользователем <b>{creditor_name}</b> "
                 f"составляет: <b>{amount:.2f} ₽</b>.\n\n"
                 f"Пожалуйста, не забудь рассчитаться! 😉",
            parse_mode="HTML"
        )
        
        # Уведомляем в группе, что напоминание успешно ушло в ЛС
        await callback.message.edit_text(
            f"✅ Напоминание о долге в размере <b>{amount:.2f} ₽</b> "
            f"успешно отправлено пользователю в личные сообщения.",
            parse_mode="HTML",
            reply_markup=None # Убираем кнопки, чтобы избежать повторных нажатий
        )
        
    except Exception as e:
        # Ошибка возникнет, если пользователь заблокировал бота или никогда не общался с ним в ЛС
        print(f"❌ Не удалось отправить сообщение в ЛС пользователю {debtor_id}: {e}")
        await callback.message.edit_text(
            f"❌ <b>Не удалось отправить напоминание.</b>\n\n"
            f"Telegram запрещает ботам писать первыми. Пользователь должен перейти в бота "
            f"и нажать кнопку <b>Старт</b> в личных сообщениях, после чего функция заработает.",
            parse_mode="HTML",
            reply_markup=None
        )


# =========================================================
# ЗАПУСК
# =========================================================

async def main():
    await init_db()

    await setup_bot_commands()

    print("✅ База данных готова")
    print("📋 Меню команд настроено")
    print("🤖 Бот запущен...")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

