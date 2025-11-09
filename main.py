import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# === КОНФИГ ===
PASSENGER_TOKEN = os.getenv("PASSENGER_BOT_TOKEN")
DRIVER_TOKEN = os.getenv("DRIVER_BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
logging.basicConfig(level=logging.INFO)

passenger_bot = Bot(token=PASSENGER_TOKEN)
driver_bot = Bot(token=DRIVER_TOKEN)
passenger_dp = Dispatcher(storage=MemoryStorage())
driver_dp = Dispatcher(storage=MemoryStorage())

# === СОСТОЯНИЯ ===
class PassengerOrder(StatesGroup):
    from_address = State()
    to_address = State()
    comment = State()
    luggage = State()
    child = State()
    confirm = State()

class DriverOffer(StatesGroup):
    car_model = State()
    price = State()

# === КЛАВИАТУРЫ ===
def get_phone_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True,
        keyboard=[[KeyboardButton(text="Поделиться номером", request_contact=True)]])

def get_main_passenger_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="Заказать такси")],
        [KeyboardButton(text="Мои заказы")],
        [KeyboardButton(text="Отменить заказ")],
        [KeyboardButton(text="Чат с админом")]
    ])

def get_main_driver_kb(is_admin=False):
    buttons = [
        [KeyboardButton(text="Активные заказы")],
        [KeyboardButton(text="Отменить поездку")],
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

def get_confirm_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подтвердить", callback_data="confirm_yes"),
         InlineKeyboardButton(text="Отменить", callback_data="confirm_no")]
    ])

# === УТИЛИТЫ ===
def is_admin(user_id): return user_id == ADMIN_ID

async def get_user(tg_id):
    res = supabase.table("users").select("*").eq("telegram_id", tg_id).execute()
    return res.data[0] if res.data else None

async def create_user(tg_id, role, name=None, phone=None):
    data = {"telegram_id": tg_id, "role": role, "name": name or "Не указано", "phone": phone or "", "blocked": False}
    return supabase.table("users").insert(data).execute().data[0]

async def notify_drivers(order, data):
    drivers = supabase.table("users").select("telegram_id").eq("role", "driver").execute().data
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Сделать предложение", callback_data=f"offer_{order['id']}")]])
    for d in drivers:
        try:
            await driver_bot.send_message(d["telegram_id"],
                f"Новый заказ!\n"
                f"От: {data['from_address']}\n"
                f"Куда: {data['to_address']}\n"
                f"Комментарий: {data['comment'] or '—'}\n"
                f"Багаж: {'Да' if data['luggage'] else 'Нет'}\n"
                f"Ребёнок: {'Да' if data['child'] else 'Нет'}",
                reply_markup=kb)
        except Exception as e: print(f"[ОШИБКА] Уведомление {d['telegram_id']}: {e}")

# === ПАССАЖИР ===
@passenger_dp.message(Command("start"))
async def passenger_start(message: types.Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    if not user:
        await create_user(message.from_user.id, "passenger", message.from_user.full_name)
        await message.answer("Привет! Поделись номером для работы с ботом:", reply_markup=get_phone_kb())
    else:
        if user["blocked"]: await message.answer("Вы заблокированы."); return
        await message.answer("Привет! Что хотите?", reply_markup=get_main_passenger_kb())

@passenger_dp.message(F.contact)
async def passenger_contact(message: types.Message):
    supabase.table("users").update({"phone": message.contact.phone_number}).eq("telegram_id", message.from_user.id).execute()
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
    await state.update_data(luggage=call.data == "luggage_yes")
    await state.set_state(PassengerOrder.child)
    await call.message.edit_text(f"Багаж: {'Да' if call.data == 'luggage_yes' else 'Нет'}\nРебёнок?", reply_markup=get_child_kb())

@passenger_dp.callback_query(F.data.startswith("child_"))
async def order_child(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    data["child"] = call.data == "child_yes"
    await state.set_state(PassengerOrder.confirm)
    text = (
        f"**Проверьте заказ:**\n\n"
        f"**Откуда:** {data['from_address']}\n"
        f"**Куда:** {data['to_address']}\n"
        f"**Комментарий:** {data['comment'] or '—'}\n"
        f"**Багаж:** {'Да' if data['luggage'] else 'Нет'}\n"
        f"**Ребёнок:** {'Да' if data['child'] else 'Нет'}\n\n"
        f"Подтвердить создание заказа?"
    )
    await call.message.edit_text(text, reply_markup=get_confirm_kb(), parse_mode="Markdown")

@passenger_dp.callback_query(F.data == "confirm_yes")
async def confirm_order(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    order = supabase.table("orders").insert({
        "passenger_id": call.from_user.id, "from_address": data["from_address"], "to_address": data["to_address"],
        "comment": data["comment"], "luggage": data["luggage"], "child": data["child"], "status": "new"
    }).execute().data[0]
    await notify_drivers(order, data)
    await call.message.edit_text("Заказ создан и отправлен водителям!")
    await call.message.answer("Выберите действие:", reply_markup=get_main_passenger_kb())

@passenger_dp.callback_query(F.data == "confirm_no")
async def cancel_order_creation(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Заказ отменён.")
    await call.message.answer("Выберите действие:", reply_markup=get_main_passenger_kb())

@passenger_dp.message(F.text == "Отменить заказ")
async def cancel_order_by_passenger(message: types.Message):
    orders = supabase.table("orders").select("id").eq("passenger_id", message.from_user.id).in_("status", ["new", "accepted"]).execute().data
    if not orders: await message.answer("У вас нет активных заказов."); return
    supabase.table("orders").update({"status": "canceled"}).eq("id", orders[0]["id"]).execute()
    await message.answer(f"Заказ #{orders[0]['id']} отменён.")
    offer = supabase.table("offers").select("driver_id").eq("order_id", orders[0]["id"]).eq("accepted", True).execute().data
    if offer: await driver_bot.send_message(offer[0]["driver_id"], f"Пассажир отменил заказ #{orders[0]['id']}.")

# === ВОДИТЕЛЬ ===
@driver_dp.message(Command("start"))
async def driver_start(message: types.Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    if not user:
        await create_user(message.from_user.id, "driver", message.from_user.full_name)
        await message.answer("Привет, водитель! Поделись номером для работы:", reply_markup=get_phone_kb())
    else:
        if user["blocked"]: await message.answer("Вы заблокированы."); return
        await message.answer("Привет, водитель! Что хотите?", reply_markup=get_main_driver_kb(is_admin(message.from_user.id)))

@driver_dp.message(F.contact)
async def driver_contact(message: types.Message):
    supabase.table("users").update({"phone": message.contact.phone_number}).eq("telegram_id", message.from_user.id).execute()
    await message.answer("Номер сохранён!", reply_markup=get_main_driver_kb(is_admin(message.from_user.id)))

@driver_dp.callback_query(F.data.startswith("offer_"))
async def driver_offer_start(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(order_id=int(call.data.split("_")[1]))
    await state.set_state(DriverOffer.car_model)
    await call.message.edit_text("Марка и модель авто:")

@driver_dp.message(DriverOffer.car_model)
async def driver_car(message: types.Message, state: FSMContext):
    await state.update_data(car_model=message.text)
    await state.set_state(DriverOffer.price)
    await message.answer("Стоимость поездки (руб):")

@driver_dp.message(DriverOffer.price)
async def driver_price(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): await message.answer("Только цифры!"); return
    data = await state.get_data(); await state.clear()
    offer = supabase.table("offers").insert({
        "order_id": data["order_id"], "driver_id": message.from_user.id,
        "car_model": data["car_model"], "price": int(message.text), "rejected": False
    }).execute().data[0]
    order = supabase.table("orders").select("passenger_id").eq("id", data["order_id"]).execute().data[0]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Принять", callback_data=f"accept_{offer['id']}"),
         InlineKeyboardButton(text="Отклонить", callback_data=f"reject_{offer['id']}")]
    ])
    await passenger_bot.send_message(order["passenger_id"],
        f"Новое предложение!\nАвто: {data['car_model']}\nЦена: {message.text} ₽", reply_markup=kb)
    await message.answer("Предложение отправлено!", reply_markup=get_main_driver_kb(is_admin(message.from_user.id)))

@passenger_dp.callback_query(F.data.startswith("accept_"))
async def accept_offer(call: types.CallbackQuery):
    offer_id = int(call.data.split("_")[1])
    offer = supabase.table("offers").select("*").eq("id", offer_id).execute().data[0]
    if offer.get("rejected", False): await call.answer("Предложение отклонено."); return
    supabase.table("offers").update({"accepted": True}).eq("id", offer_id).execute()
    supabase.table("orders").update({"status": "accepted"}).eq("id", offer["order_id"]).execute()
    driver = await get_user(offer["driver_id"]); passenger = await get_user(call.from_user.id)
    await passenger_bot.send_message(call.from_user.id, f"Заказ принят!\nВодитель: {driver['name']}\nТелефон: {driver['phone']}")
    await driver_bot.send_message(offer["driver_id"], f"Заказ принят!\nПассажир: {passenger['name']}\nТелефон: {passenger['phone']}")

@passenger_dp.callback_query(F.data.startswith("reject_"))
async def reject_offer(call: types.CallbackQuery):
    offer_id = int(call.data.split("_")[1])
    offer = supabase.table("offers").select("*").eq("id", offer_id).execute().data[0]
    supabase.table("offers").update({"rejected": True}).eq("id", offer_id).execute()
    await driver_bot.send_message(offer["driver_id"], f"Ваше предложение отклонено.\nАвто: {offer['car_model']} — {offer['price']} ₽")
    await call.message.edit_text(f"Предложение отклонено.\nАвто: {offer['car_model']} — {offer['price']} ₽")

@driver_dp.message(F.text == "Отменить поездку")
async def cancel_trip_by_driver(message: types.Message):
    offers = supabase.table("offers").select("order_id").eq("driver_id", message.from_user.id).eq("accepted", True).execute().data
    if not offers: await message.answer("У вас нет принятых заказов."); return
    order_id = offers[0]["order_id"]
    supabase.table("offers").update({"accepted": False}).eq("order_id", order_id).execute()
    supabase.table("orders").update({"status": "new"}).eq("id", order_id).execute()
    order = supabase.table("orders").select("*").eq("id", order_id).execute().data[0]
    await passenger_bot.send_message(order["passenger_id"], "Водитель отменил поездку. Заказ снова активен!")
    await message.answer(f"Поездка отменена. Заказ #{order_id} снова доступен другим водителям.")
    await notify_drivers(order, {
        "from_address": order["from_address"], "to_address": order["to_address"],
        "comment": order["comment"], "luggage": order["luggage"], "child": order["child"]
    })

# === ЧАТ С АДМИНОМ ===
@passenger_dp.message(F.text == "Чат с админом")
async def chat_admin_passenger(message: types.Message):
    await message.answer("Пишите админу:", reply_markup=types.ReplyKeyboardRemove())
    await passenger_bot.copy_message(ADMIN_ID, message.chat.id, message.message_id)

@driver_dp.message(F.text == "Чат с админом")
async def chat_admin_driver(message: types.Message):
    await message.answer("Пишите админу:", reply_markup=types.ReplyKeyboardRemove())
    await driver_bot.copy_message(ADMIN_ID, message.chat.id, message.message_id)

# === АДМИН-ПАНЕЛЬ ===
@driver_dp.message(F.text == "Админ-панель")
async def admin_panel(message: types.Message):
    if not is_admin(message.from_user.id): await message.answer("Доступ запрещён."); return
    await message.answer("Админ-панель", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="Пользователи")], [KeyboardButton(text="Заказы")], [KeyboardButton(text="Назад")]
    ]))

@driver_dp.message(F.text == "Пользователи")
async def list_users(message: types.Message):
    if not is_admin(message.from_user.id): return
    users = supabase.table("users").select("telegram_id,name,role,blocked").execute().data
    text = "Пользователи:\n" + "\n".join(f"{u['name']} (@{u['telegram_id']}) — {u['role']} {'Заблокирован' if u['blocked'] else 'Активен'}" for u in users)
    await message.answer(text)

@driver_dp.message(F.text == "Заказы")
async def list_orders(message: types.Message):
    if not is_admin(message.from_user.id): return
    orders = supabase.table("orders").select("*").execute().data
    text = "Заказы:\n" + "\n".join(f"ID {o['id']} | {o['from_address']} → {o['to_address']} | {o['status']}" for o in orders)
    await message.answer(text)

# === ЗАПУСК ===
async def main():
    await asyncio.gather(
        passenger_dp.start_polling(passenger_bot),
        driver_dp.start_polling(driver_bot)
    )

if __name__ == "__main__":
    asyncio.run(main())
