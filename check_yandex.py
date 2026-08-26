# -*- coding: utf-8 -*-
"""
check_yandex.py — проверка ключей Яндекса.
Запуск: python3 check_yandex.py
Скрипт скажет, что именно не так: ключ, folder_id или права доступа.
"""
import httpx

from config import YC_API_KEY, YC_FOLDER_ID

print("Проверяю ключи...")
print(f"  Folder ID: {YC_FOLDER_ID!r}")
print(f"  API-ключ начинается с: {YC_API_KEY[:6]}... (длина {len(YC_API_KEY)})")
print()

# Простейшие проверки формата
if not YC_FOLDER_ID.startswith("b1g"):
    print("⚠️  ВНИМАНИЕ: Folder ID обычно начинается с 'b1g'. Возможно, ты скопировал")
    print("   ID облака (cloud) вместо ID каталога (folder). Открой каталог в консоли —")
    print("   его ID виден в адресной строке: console.yandex.cloud/folders/<ВОТ_ЭТО>")
    print()
if " " in YC_API_KEY or " " in YC_FOLDER_ID:
    print("⚠️  В ключе или Folder ID есть пробел — удали его в config.py!")
    print()

r = httpx.post(
    "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
    headers={"Authorization": f"Api-Key {YC_API_KEY}"},
    json={
        "modelUri": f"gpt://{YC_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {"temperature": 0.1, "maxTokens": "50"},
        "messages": [{"role": "user", "text": "Скажи одно слово: привет"}],
    },
    timeout=60,
)

if r.status_code == 200:
    answer = r.json()["result"]["alternatives"][0]["message"]["text"]
    print(f"✅ ВСЁ РАБОТАЕТ! YandexGPT ответил: {answer}")
else:
    print(f"❌ Ошибка {r.status_code}. Полный ответ Яндекса:")
    print(r.text)
    print()
    if r.status_code == 401:
        print("→ Значит: API-ключ неверный. Создай новый в сервисном аккаунте.")
    elif r.status_code == 403:
        print("→ Значит: у сервисного аккаунта нет роли ai.languageModels.user.")
        print("  Добавь роль: консоль → каталог → Права доступа → Назначить роли.")
    elif r.status_code == 400:
        print("→ Значит: скорее всего неверный Folder ID (см. предупреждение выше).")
