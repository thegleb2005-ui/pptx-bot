# -*- coding: utf-8 -*-
"""
config.py — ключи.
Значения ВСЕГДА берутся из переменных окружения. Хардкода секретов в коде
нет — так безопаснее, и код не попадёт с открытым токеном в git/GitHub.

При старте бот печатает в лог замаскированную диагностику по каждому
значению (длина + первые/последние символы), чтобы можно было на глаз
сверить, что реально дошло до кода, не публикуя секрет целиком.
"""
import os
import re

TOKEN_RE = re.compile(r"^\d+:[\w-]{35}$")


def _mask(value: str) -> str:
    if len(value) <= 10:
        return "*" * len(value)
    return value[:6] + "..." + value[-4:]


def _require(name: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        raise RuntimeError(
            f"[config] Переменная окружения {name} вообще НЕ ЗАДАНА на хостинге "
            f"(os.getenv вернул None). Проверь, что она реально сохранена в панели."
        )

    value = raw.strip()

    if raw != value:
        print(
            f"[config] ВНИМАНИЕ: в {name} есть лишние пробелы/переносы строк "
            f"по краям значения — они обрезаны автоматически. "
            f"Сырая длина={len(raw)!r}, после обрезки={len(value)!r}"
        )

    if not value:
        raise RuntimeError(
            f"[config] Переменная окружения {name} задана, но ПУСТАЯ. "
            f"Проверь значение в панели хостинга — похоже, оно не сохранилось."
        )

    print(f"[config] {name}: длина={len(value)}, значение={_mask(value)}")
    return value


def _require_token(name: str) -> str:
    value = _require(name)
    if not TOKEN_RE.match(value):
        raise RuntimeError(
            f"[config] {name} задан (длина {len(value)} символов, "
            f"значение {_mask(value)}), но НЕ ПРОХОДИТ проверку формата "
            f"токена Telegram (должно быть вида "
            f"1234567890:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx — цифры, "
            f"двоеточие, затем ровно 35 символов). Похоже, значение "
            f"скопировано с ошибкой: лишний символ, кавычки, обрезан "
            f"хвост или вставлен не тот текст. Перепроверь и вставь заново."
        )
    return value


def _optional(name: str) -> str:
    """Необязательная переменная — если не задана, просто пусто, без падения бота."""
    value = os.getenv(name, "").strip()
    if value:
        print(f"[config] {name}: длина={len(value)}, значение={_mask(value)}")
    return value


BOT_TOKEN = _require_token("BOT_TOKEN")
KIE_API_KEY = _require("KIE_API_KEY")

# Больше не используются в ai.py (текст/картинки теперь через kie.ai),
# но оставлены необязательными на случай, если где-то ещё пригодятся.
YC_API_KEY = _optional("YC_API_KEY")
YC_FOLDER_ID = _optional("YC_FOLDER_ID")
