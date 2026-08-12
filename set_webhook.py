# -*- coding: utf-8 -*-
"""
set_webhook.py — сообщает Telegram адрес твоего контейнера.
Запускается ОДИН РАЗ с Мака после создания контейнера:

    python3 set_webhook.py https://xxxxx.containers.yandexcloud.net

(подставь свой URL контейнера из консоли Яндекс Клауда)
"""
import sys

import httpx

from config import BOT_TOKEN

if len(sys.argv) < 2 or not sys.argv[1].startswith("https://"):
    print("Использование: python3 set_webhook.py https://АДРЕС_КОНТЕЙНЕРА")
    sys.exit(1)

url = sys.argv[1].rstrip("/") + "/webhook"

r = httpx.post(
    f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
    json={"url": url, "drop_pending_updates": True},
    timeout=30,
)
print(r.json())

info = httpx.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo", timeout=30).json()
print()
if r.json().get("ok"):
    print(f"✅ Webhook установлен: {url}")
    print("Теперь напиши боту /start в Telegram!")
else:
    print("❌ Не получилось. Ответ Telegram выше — пришли его в чат, разберёмся.")
