# -*- coding: utf-8 -*-
"""
ai.py — общение с ChatGPT (текст) и Nano Banana Pro (картинки)
через агрегатор kie.ai (https://kie.ai).
Ключ вставляется в переменную окружения KIE_API_KEY — сюда лезть не нужно.
"""
import asyncio
import json
import re

import httpx

from config import KIE_API_KEY

HEADERS = {
    "Authorization": f"Bearer {KIE_API_KEY}",
    "Content-Type": "application/json",
}

# Строку модели можно поменять на любую другую из каталога kie.ai
# (https://kie.ai/market) — просто замени значение здесь.
TEXT_MODEL = "gpt-5-2"            # ChatGPT через kie.ai (простой chat-completions эндпоинт)
IMAGE_MODEL = "nano-banana-pro"   # Nano Banana Pro через kie.ai


# ============ ПРОМПТ ДЛЯ ГЕНЕРАЦИИ СТРУКТУРЫ ПРЕЗЕНТАЦИИ ============
# Инструкции модели даём на английском (это не влияет на язык самого контента
# презентации — он задаётся отдельной строкой language_instruction ниже) —
# так модель стабильнее держит формат JSON независимо от целевого языка.

# ============ ПРОМПТ ДЛЯ ГЕНЕРАЦИИ СТРУКТУРЫ ПРЕЗЕНТАЦИИ ============
# Инструкции модели даём на английском (это не влияет на язык самого контента
# презентации — он задаётся отдельной строкой language_instruction ниже) —
# так модель стабильнее держит формат JSON независимо от целевого языка.
#
# Набор доступных типов слайдов (layout) зависит от конкретного шаблона —
# у одних шаблонов только 4 слайда-образца (text_image + bullets), у других
# 6 (+ comparison + highlights). available_layouts передаётся из bot.py,
# который заранее узнаёт это через builder.get_available_layouts().

DEFAULT_LAYOUTS = ["title", "text_image", "bullets", "final"]

LAYOUT_FIELD_DESCRIPTIONS = {
    "title": '- "title": fields TITLE (max 6 words), SUBTITLE (one short sentence).',
    "text_image": '- "text_image": fields TITLE (max 6 words), TEXT (3-4 sentences, max 350 characters), image_prompt (10-20 words, ALWAYS in English, photorealistic style description of a fitting image).',
    "bullets": '- "bullets": fields TITLE (max 6 words), BULLET_1, BULLET_2, BULLET_3 (each 1-2 sentences, max 150 characters).',
    "comparison": '- "comparison": fields TITLE (max 6 words), TEXT_1, TEXT_2 — two short paragraphs (max 200 characters each) comparing two things, options, or perspectives side by side.',
    "highlights": '- "highlights": fields TITLE (max 6 words), HIGHLIGHT_1, HIGHLIGHT_2 — two short punchy statements or key numbers/stats (max 80 characters each).',
    "final": '- "final": field TITLE (e.g. a short closing/thank-you line).',
}

EXAMPLE_SNIPPETS = {
    "title": '{{"layout": "title", "TITLE": "...", "SUBTITLE": "..."}}',
    "text_image": '{{"layout": "text_image", "TITLE": "...", "TEXT": "...", "image_prompt": "..."}}',
    "bullets": '{{"layout": "bullets", "TITLE": "...", "BULLET_1": "...", "BULLET_2": "...", "BULLET_3": "..."}}',
    "comparison": '{{"layout": "comparison", "TITLE": "...", "TEXT_1": "...", "TEXT_2": "..."}}',
    "highlights": '{{"layout": "highlights", "TITLE": "...", "HIGHLIGHT_1": "...", "HIGHLIGHT_2": "..."}}',
    "final": '{{"layout": "final", "TITLE": "..."}}',
}

PROMPT_TEMPLATE = """You are a presentation expert. Create a structure for a presentation with {n} slide(s) about: "{topic}"

If the input looks like ready-made text rather than a short topic, split THAT content across the slides instead of inventing a new topic.

Available slide layouts:
{layout_descriptions}

Rules:
1. {structure_rule}
2. {language_instruction}
3. image_prompt (if used) must ALWAYS be written in English, regardless of the content language chosen above.

Reply with STRICT JSON only, no explanation before or after:
{{
  "slides": [
    {example_slides}
  ]
}}"""

LANGUAGE_INSTRUCTIONS = {
    "ru": "Write ALL slide text content in Russian — natural, informative, no filler.",
    "en": "Write ALL slide text content in English — natural, informative, no filler.",
}


def _structure_rule(n: int, available_layouts: list) -> str:
    middle_roles = [r for r in available_layouts if r not in ("title", "final")]
    if n <= 0:
        n = 1
    if n == 1:
        return 'There is exactly 1 slide total. It must be layout "title".'
    if n == 2:
        return 'There are exactly 2 slides total: the first must be layout "title", the second must be layout "final".'
    roles_text = ", ".join(f'"{r}"' for r in middle_roles) if middle_roles else '"bullets"'
    return (
        f'There are exactly {n} slides total. The first slide must be layout "title", '
        f'the last slide must be layout "final". For the slides in between, use a mix '
        f'of these available layouts, choosing whichever fits the content best: {roles_text}.'
    )


def _extract_json(text: str) -> dict:
    """Нейросеть иногда оборачивает ответ в ```json ... ``` — вырезаем чистый JSON."""
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("В ответе нейросети нет JSON: " + text[:200])
    return json.loads(match.group(0))


def _parse_json_response(r: httpx.Response) -> dict:
    """
    Аккуратно разбирает ответ kie.ai в JSON.
    Некоторые эндпоинты kie.ai присылают ответ в формате SSE (строки вида
    "data: {...}") даже без явного запроса потока — эта функция умеет
    разобрать и обычный JSON, и такой "потоковый" вариант.
    Если разобрать не получилось вообще никак — кидает ошибку с сырым
    текстом ответа, чтобы было видно, что реально прислал сервер.
    """
    try:
        return r.json()
    except (json.JSONDecodeError, ValueError):
        pass

    candidates = []
    for line in r.text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            candidates.append(json.loads(payload))
        except json.JSONDecodeError:
            continue

    if candidates:
        for c in reversed(candidates):
            if c.get("output"):
                return c
        return candidates[-1]

    if not r.text.strip():
        raise RuntimeError(
            f"kie.ai прислал ПУСТОЙ ответ при статусе {r.status_code}. "
            f"Обычно это значит: закончились кредиты на балансе kie.ai, "
            f"выбранная модель недоступна на твоём ключе, или временный сбой "
            f"на их стороне. Проверь https://kie.ai/logs — там видно, что "
            f"реально произошло с последним запросом."
        )

    raise RuntimeError(
        f"kie.ai прислал ответ (статус {r.status_code}), который не "
        f"получилось разобрать как JSON. Начало сырого ответа: "
        f"{r.text[:500]!r}"
    )


async def generate_structure(topic: str, n_slides: int, language: str = "ru",
                              available_layouts: list | None = None) -> dict:
    """Просит ChatGPT (через kie.ai) придумать все тексты для слайдов.
    language: "ru" или "en" — язык итогового текста презентации.
    available_layouts: какие типы слайдов реально поддерживает выбранный
    шаблон (см. builder.get_available_layouts) — по умолчанию базовые 4."""
    layouts = available_layouts or DEFAULT_LAYOUTS
    layout_descriptions = "\n".join(
        LAYOUT_FIELD_DESCRIPTIONS[r] for r in layouts if r in LAYOUT_FIELD_DESCRIPTIONS
    )
    example_slides = ",\n    ".join(
        EXAMPLE_SNIPPETS[r] for r in layouts if r in EXAMPLE_SNIPPETS
    )
    prompt = PROMPT_TEMPLATE.format(
        topic=topic,
        n=n_slides,
        layout_descriptions=layout_descriptions,
        structure_rule=_structure_rule(n_slides, layouts),
        language_instruction=LANGUAGE_INSTRUCTIONS.get(language, LANGUAGE_INSTRUCTIONS["ru"]),
        example_slides=example_slides,
    )
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            f"https://api.kie.ai/{TEXT_MODEL}/v1/chat/completions",
            headers=HEADERS,
            json={
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": prompt}]}
                ],
            },
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"kie.ai (ChatGPT) ответил ошибкой {r.status_code}: {r.text[:500]}"
            )
        data = _parse_json_response(r)

    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(
            f"Не удалось найти текст ответа в данных kie.ai: {json.dumps(data)[:500]}"
        )

    return _extract_json(answer)


async def generate_image(prompt: str) -> bytes | None:
    """Генерирует картинку через Nano Banana Pro (kie.ai). Возвращает байты или None при ошибке."""
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(
                "https://api.kie.ai/api/v1/jobs/createTask",
                headers=HEADERS,
                json={
                    "model": IMAGE_MODEL,
                    "input": {
                        "prompt": prompt,
                        "image_input": [],
                        "aspect_ratio": "16:9",
                        "resolution": "1K",
                        "output_format": "png",
                    },
                },
            )
            r.raise_for_status()
            payload = _parse_json_response(r)
            if payload.get("code") != 200:
                print(f"kie.ai createTask вернул ошибку: {payload}")
                return None
            task_id = payload["data"]["taskId"]

            for _ in range(40):
                await asyncio.sleep(5)
                poll = await client.get(
                    "https://api.kie.ai/api/v1/jobs/recordInfo",
                    headers=HEADERS,
                    params={"taskId": task_id},
                )
                poll_data = _parse_json_response(poll).get("data", {})
                state = poll_data.get("state")

                if state == "success":
                    result = json.loads(poll_data["resultJson"])
                    image_url = result["resultUrls"][0]
                    img_resp = await client.get(image_url)
                    img_resp.raise_for_status()
                    return img_resp.content

                if state == "fail":
                    print(f"kie.ai: генерация картинки не удалась: {poll_data.get('failMsg')}")
                    return None

    except Exception as e:
        print(f"Ошибка генерации картинки: {e}")
    return None
