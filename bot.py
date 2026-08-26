# -*- coding: utf-8 -*-
"""
bot.py — версия для Bothost (режим polling по умолчанию).

Локально на Маке можно запустить явно в старом режиме через:
    python3 bot.py polling
Явный запуск веб-сервера (если когда-нибудь понадобится webhook):
    python3 bot.py webhook
"""
import asyncio
import io
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections import deque

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery,
                           InlineKeyboardButton, InlineKeyboardMarkup,
                           InputMediaPhoto, Message, Update)
from aiohttp import web

import db
from ai import generate_image, generate_structure
from builder import build_pptx, get_available_layouts
from config import BOT_TOKEN

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db.init_db()

# Твой Telegram ID — команда /stats работает только для него.
ADMIN_ID = 270830135

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

_seen_updates: deque = deque(maxlen=200)
active_generations: dict[int, asyncio.Task] = {}


class Flow(StatesGroup):
    choosing_language = State()
    choosing_template = State()
    choosing_slides = State()
    entering_topic = State()
    choosing_images = State()


# ============================================================
#                     ОБЩИЕ МЕЛОЧИ / КНОПКИ
# ============================================================

def _home_button(ru: bool = True) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text="🏠 В начало" if ru else "🏠 Start over",
        callback_data="go_home",
    )


def home_kb(extra_rows: list | None = None, ru: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура: сначала опциональные ряды кнопок, затем всегда — 'В начало'."""
    rows = list(extra_rows) if extra_rows else []
    rows.append([_home_button(ru)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _get_lang(state: FSMContext) -> str:
    data = await state.get_data()
    return data.get("language", "ru")


# ============================================================
#                  ШАБЛОНЫ И КАРУСЕЛЬ ПРЕВЬЮ
# ============================================================

def get_templates() -> dict:
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


def _placeholder_bytes() -> bytes:
    from PIL import Image, ImageDraw

    W, H = 1024, 576
    bg, border, icon = (235, 236, 240), (200, 202, 208), (190, 193, 199)

    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle([8, 8, W - 9, H - 9], outline=border, width=3)

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


def _carousel_card(index: int, keys: list, templates: dict, ru: bool):
    key = keys[index]
    tpl = templates[key]
    photo_bytes = None
    if tpl["preview"]:
        try:
            with open(tpl["preview"], "rb") as f:
                photo_bytes = f.read()
        except OSError:
            photo_bytes = None
    if photo_bytes is None:
        photo_bytes = _placeholder_bytes()

    label = f"Шаблон {index + 1} из {len(keys)}" if ru else f"Template {index + 1} of {len(keys)}"
    caption = f"{tpl['name']}\n\n{label}"

    nav = []
    if len(keys) > 1:
        nav.append(InlineKeyboardButton(text="◀️", callback_data="carousel_prev"))
    nav.append(InlineKeyboardButton(
        text="✅ Выбрать этот" if ru else "✅ Select this one",
        callback_data=f"tpl_{key}",
    ))
    if len(keys) > 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data="carousel_next"))

    kb = home_kb(extra_rows=[nav], ru=ru)
    return photo_bytes, caption, kb


async def send_template_carousel(target, state: FSMContext):
    ru = await _get_lang(state) == "ru"
    templates = get_templates()
    keys = list(templates.keys())
    if not keys:
        await target.answer(
            "😔 Пока нет ни одного загруженного шаблона."
            if ru else
            "😔 No templates uploaded yet."
        )
        return

    await state.update_data(tpl_keys=keys, tpl_index=0)
    photo_bytes, caption, kb = _carousel_card(0, keys, templates, ru)
    intro = "Выбери шаблон оформления (можно листать стрелками):\n\n" if ru else \
            "Choose a design template (swipe with the arrows):\n\n"
    await target.answer_photo(
        BufferedInputFile(photo_bytes, filename="preview.png"),
        caption=intro + caption,
        reply_markup=kb,
    )
    await state.set_state(Flow.choosing_template)


# ============================================================
#                ПРИВЕТСТВИЕ / СТАРТ / "В НАЧАЛО"
# ============================================================

WELCOME_TEXT = (
    "👋 <b>Привет! Я собираю готовые презентации PowerPoint по одной теме — "
    "за пару минут, без единого клика в самом PowerPoint.</b>\n\n"
    "Как это работает:\n"
    "1️⃣ Выбираешь язык презентации\n"
    "2️⃣ Выбираешь дизайн оформления — можно полистать варианты\n"
    "3️⃣ Указываешь, сколько слайдов нужно (от 1 до 20)\n"
    "4️⃣ Пишешь тему презентации\n\n"
    "Дальше я сам придумаю структуру, тексты, а по желанию — подберу картинки "
    "для слайдов. На выходе — готовый файл .pptx (и, если получится, сразу "
    "и .pdf).\n\n"
    "Жми кнопку ниже, чтобы начать 👇"
)


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Создать презентацию", callback_data="begin_creation")
    ]])
    await message.answer(WELCOME_TEXT, reply_markup=kb, parse_mode="HTML")


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return  # молча игнорируем — обычным пользователям знать о команде не нужно

    s = db.get_stats()
    lines = [
        "📊 <b>Статистика бота</b>",
        "",
        f"👤 Пользователей: {s['total_users']}",
        f"🖼 Презентаций создано: {s['total_generations']}",
        "",
        "🌐 По языкам:",
    ]
    lang_names = {"ru": "Русский", "en": "English"}
    for row in s["by_language"]:
        lines.append(f"  {lang_names.get(row['language'], row['language'] or '—')}: {row['cnt']}")

    lines.append("")
    lines.append("🎨 Топ шаблонов:")
    for row in s["top_templates"]:
        lines.append(f"  {row['template']}: {row['cnt']}")

    lines.append("")
    lines.append("🕐 Последние генерации:")
    for row in s["recent"]:
        who = f"@{row['username']}" if row["username"] else "без username"
        when = row["created_at"][:16].replace("T", " ")
        lines.append(f"  {when} — {who} — «{row['topic'][:30]}» ({row['language']}, {row['template']})")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ============================================================
#                    ПОДДЕРЖКА (без личных контактов)
# ============================================================
# /support — пользователь пишет сообщение, бот пересылает его тебе.
# Если ты ОТВЕТИШЬ (Reply) на пересланное сообщение прямо в Telegram —
# бот сам доставит твой ответ обратно этому пользователю.

awaiting_support: set[int] = set()


@dp.message(Command("support"))
async def cmd_support(message: Message, state: FSMContext):
    awaiting_support.add(message.from_user.id)
    ru = await _get_lang(state) == "ru"
    text = (
        "✉️ Напиши сообщение в поддержку (можно текст, фото или файл) — "
        "я передам его автору бота, и тебе ответят прямо здесь."
        if ru else
        "✉️ Type your message (text, photo, or file) — "
        "I'll forward it to the bot's creator, who will reply right here."
    )
    await message.answer(text)


async def _is_awaiting_support(message: Message) -> bool:
    return message.from_user.id in awaiting_support


@dp.message(_is_awaiting_support)
async def support_message_received(message: Message, state: FSMContext):
    awaiting_support.discard(message.from_user.id)
    ru = await _get_lang(state) == "ru"

    user = message.from_user
    header = (
        f"✉️ Сообщение в поддержку от {user.first_name or ''} "
        f"(@{user.username or 'без username'}, id {user.id}):"
    )
    await bot.send_message(ADMIN_ID, header)
    forwarded = await message.forward(ADMIN_ID)
    db.save_support_thread(forwarded.message_id, message.chat.id)

    await message.answer(
        "✅ Сообщение отправлено! Скоро ответим." if ru else
        "✅ Message sent! We'll get back to you soon."
    )


@dp.message(F.from_user.id == ADMIN_ID, F.reply_to_message)
async def admin_reply_to_support(message: Message):
    """Если ты отвечаешь (Reply) на пересланное сообщение поддержки —
    доставляем твой ответ обратно нужному пользователю."""
    user_chat_id = db.get_support_thread(message.reply_to_message.message_id)
    if not user_chat_id:
        return  # это не ответ на сообщение поддержки — не наше дело

    try:
        await bot.send_message(user_chat_id, f"💬 Ответ от поддержки:\n\n{message.text or message.caption or ''}")
        await message.reply("✅ Отправлено пользователю")
    except Exception as e:
        await message.reply(f"⚠️ Не удалось доставить ответ: {e}")


async def _start_creation_flow(target, state: FSMContext):
    """Первый шаг самого процесса создания — выбор языка."""
    kb = home_kb(extra_rows=[[
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en"),
    ]])
    await target.answer("🌐 На каком языке сделать презентацию?\n🌐 What language should the presentation be in?", reply_markup=kb)
    await state.set_state(Flow.choosing_language)


@dp.callback_query(F.data == "begin_creation")
async def begin_creation(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _start_creation_flow(cb.message, state)
    await cb.message.delete()
    await cb.answer()


@dp.callback_query(F.data == "restart")
async def restart(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _start_creation_flow(cb.message, state)
    await cb.answer()


@dp.callback_query(F.data == "go_home")
async def go_home(cb: CallbackQuery, state: FSMContext):
    task = active_generations.get(cb.from_user.id)
    if task and not task.done():
        task.cancel()
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Создать презентацию", callback_data="begin_creation")
    ]])
    await cb.message.answer(WELCOME_TEXT, reply_markup=kb, parse_mode="HTML")
    try:
        if not cb.message.document:
            await cb.message.delete()
    except Exception:
        pass
    await cb.answer()


# ============================================================
#            ЯЗЫК → ШАБЛОН (КАРУСЕЛЬ) → СЛАЙДЫ → ТЕМА
# ============================================================

@dp.callback_query(F.data.startswith("lang_"), Flow.choosing_language)
async def language_chosen(cb: CallbackQuery, state: FSMContext):
    language = cb.data[5:]  # "ru" или "en"
    await state.update_data(language=language)
    db.set_user_language(cb.from_user.id, language)
    await send_template_carousel(cb.message, state)
    await cb.message.delete()
    await cb.answer()


@dp.callback_query(F.data.in_(["carousel_prev", "carousel_next"]), Flow.choosing_template)
async def carousel_nav(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    keys = data.get("tpl_keys", [])
    index = data.get("tpl_index", 0)
    if not keys:
        await cb.answer()
        return

    index = (index - 1) % len(keys) if cb.data == "carousel_prev" else (index + 1) % len(keys)
    ru = data.get("language", "ru") == "ru"
    templates = get_templates()
    photo_bytes, caption, kb = _carousel_card(index, keys, templates, ru)
    media = InputMediaPhoto(media=BufferedInputFile(photo_bytes, filename="preview.png"), caption=caption)
    await cb.message.edit_media(media=media, reply_markup=kb)
    await state.update_data(tpl_index=index)
    await cb.answer()


@dp.callback_query(F.data.startswith("tpl_"), Flow.choosing_template)
async def template_chosen(cb: CallbackQuery, state: FSMContext):
    await state.update_data(template=cb.data[4:])
    ru = await _get_lang(state) == "ru"
    text = "📄 Сколько сделать слайдов? Напиши число от 1 до 20." if ru else \
           "📄 How many slides? Type a number from 1 to 20."
    await cb.message.answer(text, reply_markup=home_kb(ru=ru))
    await cb.message.delete()
    await state.set_state(Flow.choosing_slides)
    await cb.answer()


@dp.message(Flow.choosing_slides, F.text)
async def slides_chosen(message: Message, state: FSMContext):
    ru = await _get_lang(state) == "ru"
    text = message.text.strip()
    if not text.isdigit() or not (1 <= int(text) <= 20):
        err = "Нужно просто число от 1 до 20. Попробуй ещё раз:" if ru else \
              "Please enter just a number from 1 to 20. Try again:"
        await message.answer(err, reply_markup=home_kb(ru=ru))
        return

    await state.update_data(n_slides=int(text))
    prompt = (
        "✍️ Напиши тему презентации (например: «История космонавтики»)\n\n"
        "Или пришли свой готовый текст — я разобью его на слайды."
        if ru else
        "✍️ Type the presentation topic (e.g. \"History of space exploration\")\n\n"
        "Or paste your own ready-made text — I'll split it into slides."
    )
    await message.answer(prompt, reply_markup=home_kb(ru=ru))
    await state.set_state(Flow.entering_topic)


@dp.message(Flow.entering_topic, F.text)
async def topic_entered(message: Message, state: FSMContext):
    await state.update_data(topic=message.text.strip())
    ru = await _get_lang(state) == "ru"
    buttons = [[
        InlineKeyboardButton(text="🖼 Да, с картинками" if ru else "🖼 Yes, with images", callback_data="img_yes"),
        InlineKeyboardButton(text="📝 Нет, только текст" if ru else "📝 No, text only", callback_data="img_no"),
    ]]
    text = (
        "Добавить картинки, сгенерированные нейросетью?\n(с картинками дольше — примерно 1-2 минуты)"
        if ru else
        "Add AI-generated images?\n(takes longer with images — about 1-2 minutes)"
    )
    await message.answer(text, reply_markup=home_kb(extra_rows=buttons, ru=ru))
    await state.set_state(Flow.choosing_images)


# ============================================================
#                          ГЕНЕРАЦИЯ
# ============================================================

def _cancel_kb(ru: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена" if ru else "❌ Cancel", callback_data="cancel_generation")],
        [_home_button(ru)],
    ])


def _restart_kb(ru: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Создать ещё" if ru else "🚀 Create another", callback_data="restart")],
        [_home_button(ru)],
    ])


def _convert_to_pdf(pptx_bytes: bytes) -> bytes | None:
    """Конвертирует pptx в pdf через LibreOffice, ЕСЛИ он установлен на сервере.
    Если его нет — тихо возвращает None (презентация всё равно уйдёт в .pptx).
    Блокирующая функция — вызывать через run_in_executor."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        logger.warning("LibreOffice не найден на сервере — PDF-версия пропущена")
        return None

    with tempfile.TemporaryDirectory() as tmp:
        pptx_path = os.path.join(tmp, "presentation.pptx")
        with open(pptx_path, "wb") as f:
            f.write(pptx_bytes)
        try:
            subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, pptx_path],
                timeout=90, check=True, capture_output=True,
            )
        except Exception as e:
            logger.warning(f"Не удалось сконвертировать в PDF: {e}")
            return None
        pdf_path = os.path.join(tmp, "presentation.pdf")
        if os.path.isfile(pdf_path):
            with open(pdf_path, "rb") as f:
                return f.read()
    return None


async def _generate_presentation(status_msg: Message, data: dict, with_images: bool, user_id: int):
    ru = data.get("language", "ru") == "ru"
    kb = _cancel_kb(ru)
    try:
        await status_msg.edit_text(
            "⏳ Придумываю структуру презентации..." if ru else "⏳ Coming up with the presentation structure...",
            reply_markup=kb,
        )
        template_path = os.path.join(TEMPLATES_DIR, data["template"], "template.pptx")
        available_layouts = get_available_layouts(template_path)
        structure = await generate_structure(
            data["topic"], data["n_slides"], data.get("language", "ru"), available_layouts
        )

        images = {}
        if with_images:
            await status_msg.edit_text(
                "🎨 Тексты готовы! Рисую картинки..." if ru else "🎨 Text is ready! Drawing images...",
                reply_markup=kb,
            )
            tasks = {}
            for i, slide in enumerate(structure["slides"]):
                prompt = slide.get("image_prompt")
                if slide.get("layout") == "text_image" and prompt:
                    tasks[i] = asyncio.create_task(generate_image(prompt))
            for i, t in tasks.items():
                img = await t
                if img:
                    images[i] = img

        await status_msg.edit_text(
            "📦 Собираю файл презентации..." if ru else "📦 Assembling the presentation file...",
            reply_markup=kb,
        )
        pptx_bytes = build_pptx(template_path, structure, images)

        db.log_generation(user_id, data["topic"], data["n_slides"], data.get("language", "ru"), data["template"])

        await status_msg.edit_text(
            "📄 Готовлю PDF-версию..." if ru else "📄 Preparing the PDF version...",
            reply_markup=kb,
        )
        loop = asyncio.get_event_loop()
        pdf_bytes = await loop.run_in_executor(None, _convert_to_pdf, pptx_bytes)

        filename_base = data["topic"][:40].replace("/", " ")
        await status_msg.answer_document(
            BufferedInputFile(pptx_bytes, filename=filename_base + ".pptx"),
            caption="✅ Готово!" if ru else "✅ Done!",
            reply_markup=_restart_kb(ru),
        )
        if pdf_bytes:
            await status_msg.answer_document(BufferedInputFile(pdf_bytes, filename=filename_base + ".pdf"))
        await status_msg.delete()

    except asyncio.CancelledError:
        try:
            await status_msg.edit_text(
                "❌ Генерация отменена." if ru else "❌ Generation cancelled.",
                reply_markup=_restart_kb(ru),
            )
        except Exception:
            pass

    except Exception as e:
        logging.exception("Ошибка генерации")
        try:
            await status_msg.edit_text(
                f"😔 Что-то пошло не так: {e}" if ru else f"😔 Something went wrong: {e}",
                reply_markup=_restart_kb(ru),
            )
        except Exception:
            pass


@dp.callback_query(F.data.startswith("img_"), Flow.choosing_images)
async def images_chosen(cb: CallbackQuery, state: FSMContext):
    with_images = cb.data == "img_yes"
    data = await state.get_data()
    await state.clear()
    await cb.answer()

    ru = data.get("language", "ru") == "ru"
    status_msg = cb.message
    await status_msg.edit_text("⏳ Начинаю..." if ru else "⏳ Starting...", reply_markup=_cancel_kb(ru))

    user_id = cb.from_user.id
    task = asyncio.create_task(_generate_presentation(status_msg, data, with_images, user_id))
    active_generations[user_id] = task
    try:
        await task
    finally:
        active_generations.pop(user_id, None)


@dp.callback_query(F.data == "cancel_generation")
async def cancel_generation(cb: CallbackQuery):
    task = active_generations.get(cb.from_user.id)
    if task and not task.done():
        task.cancel()
        await cb.answer("Отменяю...")
    else:
        await cb.answer("Генерация уже завершена")


# ================== WEBHOOK-СЕРВЕР (запасной режим) ==================

async def handle_webhook(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400)

    update_id = data.get("update_id")
    if update_id in _seen_updates:
        return web.Response(text="duplicate")
    _seen_updates.append(update_id)

    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return web.Response(text="ok")


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="Bot is running")


def run_webhook_server():
    port = int(os.getenv("PORT", 8080))
    app = web.Application()
    app.router.add_post("/webhook", handle_webhook)
    app.router.add_get("/", handle_health)
    logger.info(f"Webhook-сервер запускается на порту {port}")
    web.run_app(app, host="0.0.0.0", port=port)


async def run_polling():
    await bot.delete_webhook(drop_pending_updates=True)
    print("Бот запущен в режиме polling! Ctrl+C для остановки.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "webhook":
        run_webhook_server()
    else:
        asyncio.run(run_polling())
