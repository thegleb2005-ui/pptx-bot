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
# ВАЖНО: gpt-5-2 использует простой стандартный chat-completions эндпоинт.
# Модели с пометкой "(response)" в каталоге kie.ai (gpt-5-4, gpt-5-5,
# gpt-5-6-*) используют другой эндпоинт (/codex/v1/responses), который на
# практике оказался жёстко привязан к роли "агента-программиста Codex" —
# для генерации текста презентаций он не подходит, поэтому им не пользуемся.
TEXT_MODEL = "gpt-5-2"            # ChatGPT через kie.ai
IMAGE_MODEL = "nano-banana-pro"   # Nano Banana Pro через kie.ai

# ============ ПРОМПТ ДЛЯ ГЕНЕРАЦИИ СТРУКТУРЫ ПРЕЗЕНТАЦИИ ============
PROMPT_TEMPLATE = """Ты — эксперт по созданию презентаций. Создай структуру презентации из {n} слайдов на тему: «{topic}».

Если вместо темы дан готовый текст — разбей ЕГО содержание на слайды, ничего не выдумывая.

Доступные типы слайдов (layout):
- "title" — титульный слайд. Поля: TITLE (до 6 слов), SUBTITLE (1 короткое предложение).
- "text_image" — слайд с текстом и картинкой. Поля: TITLE (до 6 слов), TEXT (3-4 предложения, до 350 символов), image_prompt (описание подходящей картинки на английском, 10-20 слов, фотореалистичный стиль).
- "bullets" — слайд со списком. Поля: TITLE (до 6 слов), BULLET_1, BULLET_2, BULLET_3 (каждый пункт 1-2 предложения, до 150 символов).
- "final" — финальный слайд. Поле: TITLE (например «Спасибо за внимание!»).

Правила:
1. Первый слайд ВСЕГДА "title", последний ВСЕГДА "final".
2. Между ними чередуй "text_image" и "bullets" для разнообразия.
3. Всего слайдов должно быть ровно {n}.
4. Текст на русском языке, живой и информативный, без воды.
5. image_prompt — ТОЛЬКО на английском.

Ответь СТРОГО в формате JSON без каких-либо пояснений до или после:
{{
  "slides": [
    {{"layout": "title", "TITLE": "...", "SUBTITLE": "..."}},
    {{"layout": "text_image", "TITLE": "...", "TEXT": "...", "image_prompt": "..."}},
    {{"layout": "bullets", "TITLE": "...", "BULLET_1": "...", "BULLET_2": "...", "BULLET_3": "..."}},
    {{"layout": "final", "TITLE": "..."}}
  ]
}}"""


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
    kie.ai у некоторых эндпоинтов присылает ответ в формате SSE
    (строки вида "data: {...}") даже при stream=false — эта функция
    умеет разобрать и обычный JSON, и такой "потоковый" вариант.
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


async def generate_structure(topic: str, n_slides: int) -> dict:
    """Просит ChatGPT (через kie.ai) придумать все тексты для слайдов."""
    prompt = PROMPT_TEMPLATE.format(topic=topic, n=n_slides)
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

            # Ждём результат (обычно 10-60 секунд, иногда дольше)
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
                # иначе (waiting/queuing/generating) — ждём дальше

    except Exception as e:
        print(f"Ошибка генерации картинки: {e}")
    return None
