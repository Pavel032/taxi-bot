import os
import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# === ТОКЕНЫ И БАЗА ===
PASSENGER_TOKEN = os.getenv("PASSENGER_BOT_TOKEN")
DRIVER_TOKEN = os.getenv("DRIVER_BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# === Логи ===
logging.basicConfig(level=logging.INFO)

# === Боты ===
passenger_bot = Bot(token=PASSENGER_TOKEN)
driver_bot = Bot(token=DRIVER_TOKEN)

passenger_dp = Dispatcher(storage=MemoryStorage())
driver_dp = Dispatcher(storage=MemoryStorage())

# === Состояния ===
class PassengerOrder(StatesGroup):
    from_address = State()
    to_address = State()
    comment = State()
    luggage = State()
    child = State()

class DriverOffer(StatesGroup):
    car_model = State()
    price = State()

# === Клавиатуры ===
def get_phone_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True,
        keyboard=[[KeyboardButton(text="Поделиться номером", request_contact=True)]])

def get_main_passenger_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True,
        keyboard=[
            [KeyboardButton(text="Заказать такси")],
            [KeyboardButton(text="Чат с админом")],
            [KeyboardButton(text="Мои заказы")]
        ])

def get_main_driver_kb(is_admin=False):
    buttons = [
        [KeyboardButton(text="Активные заказы")],
        [KeyboardButton(text="Чат с админом")]
    ]
    if is_admin:
        buttons.append([KeyboardButton(text="Админ-панель")])
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=buttons)

def get_luggage_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да", callback_data="luggage_yes"),
         InlineKeyboardButton(text="Нет", callback_data="luggage_no")]
    ])

def get_child_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да", callback_data="child_yes"),
         InlineKeyboardButton(text="Нет", callback_data="child_no")]
    ])

# === Утилиты ===
def is_admin(user_id):
    return user_id == ADMIN_ID

async def cleanup_sessions():
    while True:
        await asyncio.sleep(3600)
        threshold = datetime.utcnow() - timedelta(hours=24)
        supabase.table("sessions").delete().lt("updated_at", threshold.isoformat()).execute()

async def get_user(tg_id):
    res = supabase.table("users").select("*").eq("telegram_id", tg_id).execute()
    return res.data[0] if res.data else None

async def create_user(tg_id, role, name=None, phone=None):
    data = {
        "telegram_id": tg_id,
        "role": role,
        "name": name or "Не указано",
        "phone": phone or "",
        "blocked": False
    }
    return supabase.table("users").insert(data).execute().data[0]

# === ПАССАЖИРСКИЙ БОТ ===
@passenger_dp.message(Command("start"))
async def passenger_start(message: types.Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    if not user:
        await create_user(message.from_user.id, "passenger", message.from_user.full_name)
        await message.answer("Привет! Поделись номером для работы с ботом:", reply_markup=get_phone_kb())
    else:
        if user["blocked"]:
            await message.answer("Вы заблокированы.")
            return
        await message.answer("Привет! Что хотите?", reply_markup=get_main_passenger_kb())

@passenger_dp.message(F.contact)
async def passenger_contact(message: types.Message):
    phone = message.contact.phone_number
    supabase.table("users").update({"phone": phone}).eq("telegram_id", message.from_user.id).execute()
    await message.answer("Номер сохранён!", reply_markup=get_main_passenger_kb())

@passenger_dp.message(F.text == "Заказать такси")
async def order_start(message: types.Message, state: FSMContext):
    await state.set_state(PassengerOrder.from_address)
    await message.answer("Откуда едем? (улица, дом)")

@passenger_dp.message(PassengerOrder.from_address)
async def order_from(message: types.Message, state: FSMContext):
    await state.update_data(from_address=message.text)
    await state.set_state(PassengerOrder.to_address)
    await message.answer("Куда едем?")

@passenger_dp.message(PassengerOrder.to_address)
async def order_to(message: types.Message, state: FSMContext):
    await state.update_data(to_address=message.text)
    await state.set_state(PassengerOrder.comment)
    await message.answer("Комментарий к заказу (необязательно):")

@passenger_dp.message(PassengerOrder.comment)
async def order_comment(message: types.Message, state: FSMContext):
    await state.update_data(comment=message.text)
    await state.set_state(PassengerOrder.luggage)
    await message.answer("Багаж?", reply_markup=get_luggage_kb())

@passenger_dp.callback_query(F.data.startswith("luggage_"))
async def order_luggage(call: types.CallbackQuery, state: FSMContext):
    luggage = call.data == "luggage_yes"
    await state.update_data(luggage=luggage)
    await state.set_state(PassengerOrder.child)
    await call.message.edit_text(f"Багаж: {'Да' if luggage else 'Нет'}\nРебёнок?", reply_markup=get_child_kb())

@passenger_dp.callback_query(F.data.startswith("child_"))
async def order_child(call: types.CallbackQuery, state: FSMContext):
    child = call.data == "child_yes"
    data = await state.get_data()
    data["child"] = child
    await state.clear()

    order = supabase.table("orders").insert({
        "passenger_id": call.from_user.id,
        "from_address": data["from_address"],
        "to_address": data["to_address"],
        "comment": data["comment"],
        "luggage": data["luggage"],
        "child": data["child"],
        "status": "new"
    }).execute().data[0]

    # === УВЕДОМЛЕНИЯ ВОДИТЕЛЯМ ===
    drivers = supabase.table("users").select("telegram_id").eq("role", "driver").execute().data
    print(f"[ЛОГ] Найдено водителей: {len(drivers)}")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сделать предложение", callback_data=f"offer_{order['id']}")]
    ])
    for driver in drivers:
        try:
            await driver_bot.send_message(
                chat_id=driver["telegram_id"],
                text=f"Новый заказ!\nОт: {data['from_address']}\nКуда: {data['to_address']}\nКомментарий: {data['comment'] or '—'}\nБагаж: {'Да' if data['luggage'] else 'Нет'}\nРебёнок: {'Да' if child else 'Нет'}",
                reply_markup=kb
            )
            print(f"[ЛОГ] Уведомление отправлено водителю {driver['telegram_id']}")
        except Exception as e:
            print(f"[ОШИБКА] Не удалось отправить водителю {driver['telegram_id']}: {e}")

    await call.message.edit_text("Заказ создан! Ожидаем предложения.")
    await call.message.answer("Выберите действие:", reply_markup=get_main_passenger_kb())

# === ВОДИТЕЛЬСКИЙ БОТ ===
@driver_dp.message(Command("start"))
async def driver_start(message: types.Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    if not user:
        await create_user(message.from_user.id, "driver", message.from_user.full_name)
        await message.answer("Привет, водитель! Поделись номером для работы:", reply_markup=get_phone_kb())
    else:
        if user["blocked"]:
            await message.answer("Вы заблокированы.")
            return
        await message.answer("Привет, водитель! Что хотите?", reply_markup=get_main_driver_kb(is_admin(message.from_user.id)))

@driver_dp.message(F.contact)
async def driver_contact(message: types.Message):
    phone = message.contact.phone_number
    supabase.table("users").update({"phone": phone}).eq("telegram_id", message.from_user.id).execute()
    await message.answer("Номер сохранён!", reply_markup=get_main_driver_kb(is_admin(message.from_user.id)))

@driver_dp.callback_query(F.data.startswith("offer_"))
async def driver_offer_start(call: types.CallbackQuery, state: FSMContext):
    order_id = int(call.data.split("_")[1])
    await state.update_data(order_id=order_id)
    await state.set_state(DriverOffer.car_model)
    await call.message.edit_text("Марка и модель авто:")

@driver_dp.message(DriverOffer.car_model)
async def driver_car(message: types.Message, state: FSMContext):
    await state.update_data(car_model=message.text)
    await state.set_state(DriverOffer.price)
    await message.answer("Стоимость поездки (руб):")

@driver_dp.message(DriverOffer.price)
async def driver_price(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Только цифры!")
        return
    data = await state.get_data()
    await state.clear()

    offer = supabase.table("offers").insert({
        "order_id": data["order_id"],
        "driver_id": message.from_user.id,
        "car_model": data["car_model"],
        "price": int(message.text)
    }).execute().data[0]

    order = supabase.table("orders").select("passenger_id").eq("id", data["order_id"]).execute().data[0]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{data['car_model']} — {message.text} ₽", callback_data=f"accept_{offer['id']}")]
    ])
    await passenger_bot.send_message(
        chat_id=order["passenger_id"],
        text=f"Новое предложение!\nАвто: {data['car_model']}\nЦена: {message.text} ₽",
        reply_markup=kb
    )
    await message.answer("Предложение отправлено!", reply_markup=get_main_driver_kb(is_admin(message.from_user.id)))

# === Остальной код (приём, чат, админ) ===
@passenger_dp.callback_query(F.data.startswith("accept_"))
async def accept_offer(call: types.CallbackQuery):
    offer_id = int(call.data.split("_")[1])
    offer = supabase.table("offers").select("*").eq("id", offer_id).execute().data[0]
    supabase.table("offers").update({"accepted": True}).eq("id", offer_id).execute()
    supabase.table("orders").update({"status": "accepted"}).eq("id", offer["order_id"]).execute()

    order = supabase.table("orders").select("passenger_id").eq("id", offer["order_id"]).execute().data[0]
    supabase.table("chats").insert({
        "order_id": offer["order_id"],
        "driver_id": offer["driver_id"],
        "passenger_id": order["passenger_id"]
    }).execute()

    driver = await get_user(offer["driver_id"])
    passenger = await get_user(order["passenger_id"])

    chat_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Чат с водителем", callback_data=f"chat_driver_{offer['order_id']}")],
        [InlineKeyboardButton(text="Закрыть чат", callback_data=f"close_chat_{offer['order_id']}")]
    ])
    await passenger_bot.send_message(call.from_user.id, f"Заказ принят!\nВодитель: {driver['name']}\nТелефон: {driver['phone']}", reply_markup=chat_kb)
    await driver_bot.send_message(offer["driver_id"], f"Заказ принят!\nПассажир: {passenger['name']}\nТелефон: {passenger['phone']}", reply_markup=chat_kb)

@passenger_dp.callback_query(F.data.startswith("chat_driver_"))
async def open_chat_passenger(call: types.CallbackQuery):
    order_id = int(call.data.split("_")[2])
    chat = supabase.table("chats").select("*").eq("order_id", order_id).execute().data[0]
    if chat["closed"]:
        await call.answer("Чат закрыт")
        return
    await call.message.edit_text("Пишите водителю (фото, голос, документ):")

@driver_dp.callback_query(F.data.startswith("chat_driver_"))
async def open_chat_driver(call: types.CallbackQuery):
    order_id = int(call.data.split("_")[2])
    chat = supabase.table("chats").select("*").eq("order_id", order_id).execute().data[0]
    if chat["closed"]:
        await call.answer("Чат закрыт")
        return
    await call.message.edit_text("Пишите пассажиру:")

@passenger_dp.message(F.chat.type == "private", F.content_type.in_({"text", "photo", "voice", "document"}))
async def forward_to_driver(message: types.Message):
    chats = supabase.table("chats").select("*").eq("passenger_id", message.from_user.id).execute().data
    if not chats or chats[0]["closed"]:
        return
    chat = chats[0]
    await driver_bot.copy_message(chat_id=chat["driver_id"], from_chat_id=message.chat.id, message_id=message.message_id)

@driver_dp.message(F.chat.type == "private", F.content_type.in_({"text", "photo", "voice", "document"}))
async def forward_to_passenger(message: types.Message):
    chats = supabase.table("chats").select("*").eq("driver_id", message.from_user.id).execute().data
    if not chats or chats[0]["closed"]:
        return
    chat = chats[0]
    await passenger_bot.copy_message(chat_id=chat["passenger_id"], from_chat_id=message.chat.id, message_id=message.message_id)

@passenger_dp.callback_query(F.data.startswith("close_chat_"))
async def close_chat(call: types.CallbackQuery):
    order_id = int(call.data.split("_")[2])
    supabase.table("chats").update({"closed": True}).eq("order_id", order_id).execute()
    await call.message.edit_text("Чат закрыт.")

@passenger_dp.message(F.text == "Чат с админом")
async def chat_admin_passenger(message: types.Message):
    await message.answer("Пишите админу:", reply_markup=types.ReplyKeyboardRemove())
    await passenger_bot.copy_message(chat_id=ADMIN_ID, from_chat_id=message.chat.id, message_id=message.message_id)

@driver_dp.message(F.text == "Чат с админом")
async def chat_admin_driver(message: types.Message):
    await message.answer("Пишите админу:", reply_markup=types.ReplyKeyboardRemove())
    await driver_bot.copy_message(chat_id=ADMIN_ID, from_chat_id=message.chat.id, message_id=message.message_id)

@driver_dp.message(F.text == "Админ-панель")
async def admin_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return
    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="Пользователи")],
        [KeyboardButton(text="Заказы")],
        [KeyboardButton(text="Назад")]
    ])
    await message.answer("Админ-панель", reply_markup=kb)

@driver_dp.message(F.text == "Пользователи")
async def list_users(message: types.Message):
    if not is_admin(message.from_user.id): return
    users = supabase.table("users").select("telegram_id,name,role,blocked").execute().data
    text = "Пользователи:\n"
    for u in users:
        status = "Заблокирован" if u["blocked"] else "Активен"
        text += f"{u['name']} (@{u['telegram_id']}) — {u['role']} {status}\n"
    await message.answer(text)

@driver_dp.message(F.text == "Заказы")
async def list_orders(message: types.Message):
    if not is_admin(message.from_user.id): return
    orders = supabase.table("orders").select("*").execute().data
    text = "Заказы:\n"
    for o in orders:
        text += f"ID {o['id']} | {o['from_address']} → {o['to_address']} | {o['status']}\n"
    await message.answer(text)

async def main():
    asyncio.create_task(cleanup_sessions())
    await asyncio.gather(
        passenger_dp.start_polling(passenger_bot),
        driver_dp.start_polling(driver_bot)
    )

if __name__ == "__main__":
    asyncio.run(main())
