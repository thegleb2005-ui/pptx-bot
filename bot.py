# -*- coding: utf-8 -*-
"""
bot.py — версия для Yandex Cloud Serverless Containers (режим webhook).

Как это работает:
- Telegram присылает каждое сообщение пользователя HTTP-запросом на адрес контейнера
- Контейнер просыпается, обрабатывает запрос и засыпает
- Платим только за секунды реальной работы

Локально на Маке можно запустить в старом режиме (long polling):
    python3 bot.py polling
"""
import asyncio
import io
import logging
import os
import sys
from collections import deque

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery,
                           InlineKeyboardButton, InlineKeyboardMarkup,
                           InputMediaPhoto, Message, Update)
from aiohttp import web

from ai import generate_image, generate_structure
from builder import build_pptx
from config import BOT_TOKEN

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
def _placeholder_bytes() -> bytes:
    """Генерирует картинку-заглушку 'превью нет' прямо в памяти — без файла на
    диске (который можно забыть закинуть) и без шрифтов (которых может не
    быть в минимальном Docker-образе)."""
    from PIL import Image, ImageDraw

    W, H = 1024, 576
    bg = (235, 236, 240)
    border = (200, 202, 208)
    icon = (190, 193, 199)

    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle([8, 8, W - 9, H - 9], outline=border, width=3)

    # простая иконка "картинка": рамка + солнце + горы
    fw, fh = 360, 220
    fx, fy = (W - fw) // 2, (H - fh) // 2
    draw.rounded_rectangle([fx, fy, fx + fw, fy + fh], radius=14, outline=icon, width=6)
    draw.ellipse([fx + 40, fy + 35, fx + 90, fy + 85], outline=icon, width=6)
    draw.polygon(
        [(fx + 20, fy + fh - 20), (fx + 140, fy + 90), (fx + 210, fy + 150),
         (fx + 270, fy + 110), (fx + fw - 20, fy + fh - 20)],
        outline=icon, width=6,
    )

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def get_templates() -> dict:
    """
    Сканирует папку templates/ и находит все подпапки с файлом template.pptx.
    Чтобы добавить новый шаблон — создай папку templates/имя_шаблона/,
    положи туда template.pptx (обязательно), и по желанию:
      - name.txt   — название для кнопки (первая строка файла)
      - preview.png / preview.jpg / preview.jpeg — картинка-превью для листания
    Если preview нет — вместо неё показывается общая заглушка.
    """
    templates = {}
    if not os.path.isdir(TEMPLATES_DIR):
        return templates

    for entry in sorted(os.listdir(TEMPLATES_DIR)):
        folder = os.path.join(TEMPLATES_DIR, entry)
        pptx_path = os.path.join(folder, "template.pptx")
        if not os.path.isfile(pptx_path):
            continue

        name_file = os.path.join(folder, "name.txt")
        if os.path.isfile(name_file):
            with open(name_file, "r", encoding="utf-8") as f:
                display_name = f.readline().strip()
        else:
            display_name = "🎨 " + entry.replace("_", " ").replace("-", " ").title()

        preview_path = None
        for ext in ("png", "jpg", "jpeg"):
            candidate = os.path.join(folder, f"preview.{ext}")
            if os.path.isfile(candidate):
                preview_path = candidate
                break

        templates[entry] = {"name": display_name, "preview": preview_path}

    return templates

# Защита от повторной обработки: Telegram может прислать то же сообщение
# ещё раз, если ответ шёл долго (генерация занимает 1-2 минуты)
_seen_updates: deque = deque(maxlen=200)


class Flow(StatesGroup):
    choosing_template = State()
    choosing_slides = State()
    entering_topic = State()
    choosing_images = State()


def _carousel_card(index: int, keys: list, templates: dict):
    """Возвращает (байты_картинки, подпись, клавиатура) для карточки шаблона по индексу."""
    key = keys[index]
    tpl = templates[key]
    photo_bytes = None
    if tpl["preview"]:
        try:
            with open(tpl["preview"], "rb") as f:
                photo_bytes = f.read()
        except OSError:
            photo_bytes = None  # файл когда-то был указан, но пропал — не падаем
    if photo_bytes is None:
        photo_bytes = _placeholder_bytes()
    caption = f"{tpl['name']}\n\nШаблон {index + 1} из {len(keys)}"
    nav = []
    if len(keys) > 1:
        nav.append(InlineKeyboardButton(text="◀️", callback_data="carousel_prev"))
    nav.append(InlineKeyboardButton(text="✅ Выбрать этот", callback_data=f"tpl_{key}"))
    if len(keys) > 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data="carousel_next"))
    kb = InlineKeyboardMarkup(inline_keyboard=[nav])
    return photo_bytes, caption, kb


async def send_template_carousel(target, state: FSMContext):
    """target — Message (для answer_photo) или CallbackQuery.message."""
    templates = get_templates()
    keys = list(templates.keys())
    if not keys:
        await target.answer(
            "😔 Пока нет ни одного загруженного шаблона. Добавь хотя бы один в папку "
            "templates/ и передеплой бота."
        )
        return

    await state.update_data(tpl_keys=keys, tpl_index=0)
    photo_bytes, caption, kb = _carousel_card(0, keys, templates)
    await target.answer_photo(
        BufferedInputFile(photo_bytes, filename="preview.png"),
        caption="Выбери шаблон оформления (можно листать стрелками):\n\n" + caption,
        reply_markup=kb,
    )
    await state.set_state(Flow.choosing_template)


WELCOME_TEXT = (
    "👋 <b>Привет! Я собираю готовые презентации PowerPoint по одной теме — "
    "за пару минут, без единого клика в самом PowerPoint.</b>\n\n"
    "Как это работает:\n"
    "1️⃣ Выбираешь дизайн оформления — можно полистать варианты\n"
    "2️⃣ Указываешь, сколько слайдов нужно\n"
    "3️⃣ Пишешь тему презентации\n\n"
    "Дальше я сам придумаю структуру, тексты, а по желанию — подберу картинки "
    "для слайдов. На выходе — готовый файл .pptx, который можно сразу открыть "
    "и при желании доработать.\n\n"
    "Жми кнопку ниже, чтобы начать 👇"
)


# ---------- /start ----------
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Создать презентацию", callback_data="begin_creation")
    ]])
    await message.answer(WELCOME_TEXT, reply_markup=kb, parse_mode="HTML")


# ---------- кнопка "Создать презентацию" на приветственном экране ----------
@dp.callback_query(F.data == "begin_creation")
async def begin_creation(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await send_template_carousel(cb.message, state)
    await cb.message.delete()
    await cb.answer()


# ---------- кнопка "Создать ещё" после готовой презентации ----------
@dp.callback_query(F.data == "restart")
async def restart(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await send_template_carousel(cb.message, state)
    await cb.answer()


# ---------- листание карусели ----------
@dp.callback_query(F.data.in_(["carousel_prev", "carousel_next"]), Flow.choosing_template)
async def carousel_nav(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    keys = data.get("tpl_keys", [])
    index = data.get("tpl_index", 0)
    if not keys:
        await cb.answer()
        return

    if cb.data == "carousel_prev":
        index = (index - 1) % len(keys)
    else:
        index = (index + 1) % len(keys)

    templates = get_templates()
    photo_bytes, caption, kb = _carousel_card(index, keys, templates)
    media = InputMediaPhoto(
        media=BufferedInputFile(photo_bytes, filename="preview.png"),
        caption=caption,
    )
    await cb.message.edit_media(media=media, reply_markup=kb)
    await state.update_data(tpl_index=index)
    await cb.answer()


# ---------- выбор шаблона ----------
@dp.callback_query(F.data.startswith("tpl_"), Flow.choosing_template)
async def template_chosen(cb: CallbackQuery, state: FSMContext):
    await state.update_data(template=cb.data[4:])
    buttons = [[
        InlineKeyboardButton(text=f"{n} слайдов", callback_data=f"n_{n}")
        for n in (5, 7, 10)
    ]]
    await cb.message.answer(
        "📄 Сколько слайдов сделать?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.message.delete()
    await state.set_state(Flow.choosing_slides)
    await cb.answer()


# ---------- выбор количества слайдов ----------
@dp.callback_query(F.data.startswith("n_"), Flow.choosing_slides)
async def slides_chosen(cb: CallbackQuery, state: FSMContext):
    await state.update_data(n_slides=int(cb.data[2:]))
    await cb.message.edit_text(
        "✍️ Напиши тему презентации (например: «История космонавтики»)\n\n"
        "Или пришли свой готовый текст — я разобью его на слайды."
    )
    await state.set_state(Flow.entering_topic)
    await cb.answer()


# ---------- ввод темы ----------
@dp.message(Flow.entering_topic, F.text)
async def topic_entered(message: Message, state: FSMContext):
    await state.update_data(topic=message.text.strip())
    buttons = [[
        InlineKeyboardButton(text="🖼 Да, с картинками", callback_data="img_yes"),
        InlineKeyboardButton(text="📝 Нет, только текст", callback_data="img_no"),
    ]]
    await message.answer(
        "Добавить картинки, сгенерированные нейросетью?\n"
        "(с картинками дольше — примерно 1-2 минуты)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(Flow.choosing_images)


# ---------- генерация ----------
@dp.callback_query(F.data.startswith("img_"), Flow.choosing_images)
async def images_chosen(cb: CallbackQuery, state: FSMContext):
    with_images = cb.data == "img_yes"
    data = await state.get_data()
    await state.clear()
    await cb.message.edit_text("⏳ Генерирую презентацию... Это займёт 1-2 минуты.")
    await cb.answer()

    try:
        structure = await generate_structure(data["topic"], data["n_slides"])

        images = {}
        if with_images:
            await cb.message.edit_text("⏳ Тексты готовы! Рисую картинки... 🎨")
            tasks = {}
            for i, slide in enumerate(structure["slides"]):
                prompt = slide.get("image_prompt")
                if slide.get("layout") == "text_image" and prompt:
                    tasks[i] = asyncio.create_task(generate_image(prompt))
            for i, task in tasks.items():
                img = await task
                if img:
                    images[i] = img

        template_path = os.path.join(TEMPLATES_DIR, data["template"], "template.pptx")
        pptx_bytes = build_pptx(template_path, structure, images)

        restart_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🚀 Создать ещё", callback_data="restart")
        ]])

        filename = data["topic"][:40].replace("/", " ") + ".pptx"
        await cb.message.answer_document(
            BufferedInputFile(pptx_bytes, filename=filename),
            caption="✅ Готово!",
            reply_markup=restart_kb,
        )
        await cb.message.delete()

    except Exception as e:
        logging.exception("Ошибка генерации")
        restart_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="restart")
        ]])
        await cb.message.edit_text(
            f"😔 Что-то пошло не так: {e}",
            reply_markup=restart_kb,
        )


# ================== WEBHOOK-СЕРВЕР ==================

async def handle_webhook(request: web.Request) -> web.Response:
    """Принимает сообщение от Telegram и передаёт его боту."""
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400)

    update_id = data.get("update_id")
    if update_id in _seen_updates:
        # Telegram прислал повтор — отвечаем ОК, но не обрабатываем заново
        return web.Response(text="duplicate")
    _seen_updates.append(update_id)

    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return web.Response(text="ok")


async def handle_health(request: web.Request) -> web.Response:
    """Проверка «жив ли контейнер» — можно открыть URL в браузере."""
    return web.Response(text="Bot is running")


def run_webhook_server():
    port = int(os.getenv("PORT", 8080))
    app = web.Application()
    app.router.add_post("/webhook", handle_webhook)
    app.router.add_get("/", handle_health)
    logger.info(f"Webhook-сервер запускается на порту {port}")
    web.run_app(app, host="0.0.0.0", port=port)


async def run_polling():
    """Локальный режим для тестов на Маке: python3 bot.py polling"""
    await bot.delete_webhook(drop_pending_updates=True)
    print("Бот запущен в режиме polling! Ctrl+C для остановки.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    # По умолчанию — polling (не нужен домен/порт/webhook).
    # Явно запросить webhook-режим: python3 bot.py webhook
    if len(sys.argv) > 1 and sys.argv[1] == "webhook":
        run_webhook_server()
    else:
        asyncio.run(run_polling())
