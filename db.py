# -*- coding: utf-8 -*-
"""
db.py — простая база данных бота на SQLite (файл, не отдельный сервер).

Хранит: пользователей, историю генераций, и ЗАГОТОВКУ под будущие платежи
(таблица payments пока не используется — бот бесплатный для всех, но
структура уже готова, чтобы потом просто начать в неё писать).

Где лежит файл базы:
- Если задана переменная окружения DATA_DIR — база кладётся туда
  (на Bothost, судя по их же логам сборки, DATA_DIR=/app/data создаётся
  автоматически на тарифах с постоянным диском — то есть просто работает).
- Если DATA_DIR не задана (например, локально на Маке) — база лежит
  рядом с файлами бота.
"""
import os
import sqlite3
from datetime import datetime, timezone

DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(__file__))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bot.sqlite3")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    """Создаёт таблицы, если их ещё нет. Безопасно вызывать при каждом старте."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                language TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                presentations_count INTEGER NOT NULL DEFAULT 0,
                is_paid INTEGER NOT NULL DEFAULT 0,
                plan TEXT,
                subscription_until TEXT
            )
        """)
        # Заготовка под оплаты — пока никто сюда не пишет, бот бесплатный.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                amount REAL,
                currency TEXT,
                provider TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                raw_payload TEXT,
                FOREIGN KEY (telegram_id) REFERENCES users (telegram_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS generations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                topic TEXT,
                n_slides INTEGER,
                language TEXT,
                template TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (telegram_id) REFERENCES users (telegram_id)
            )
        """)
        conn.commit()


def upsert_user(telegram_id: int, username: str | None, first_name: str | None) -> None:
    """Создаёт пользователя при первом /start, обновляет last_seen при повторных."""
    now = _now()
    with _connect() as conn:
        conn.execute("""
            INSERT INTO users (telegram_id, username, first_name, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_seen = excluded.last_seen
        """, (telegram_id, username, first_name, now, now))
        conn.commit()


def set_user_language(telegram_id: int, language: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET language = ?, last_seen = ? WHERE telegram_id = ?",
            (language, _now(), telegram_id),
        )
        conn.commit()


def log_generation(telegram_id: int, topic: str, n_slides: int, language: str, template: str) -> None:
    """Вызывать при КАЖДОЙ успешно собранной презентации."""
    now = _now()
    with _connect() as conn:
        conn.execute("""
            INSERT INTO generations (telegram_id, topic, n_slides, language, template, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (telegram_id, topic, n_slides, language, template, now))
        conn.execute("""
            UPDATE users SET presentations_count = presentations_count + 1, last_seen = ?
            WHERE telegram_id = ?
        """, (now, telegram_id))
        conn.commit()


def get_user(telegram_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        return dict(row) if row else None


def get_stats() -> dict:
    """Сводка для команды /stats — только для владельца бота."""
    with _connect() as conn:
        total_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        total_generations = conn.execute("SELECT COUNT(*) AS c FROM generations").fetchone()["c"]
        by_language = conn.execute(
            "SELECT language, COUNT(*) AS cnt FROM generations GROUP BY language"
        ).fetchall()
        top_templates = conn.execute(
            "SELECT template, COUNT(*) AS cnt FROM generations "
            "GROUP BY template ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
        recent = conn.execute("""
            SELECT g.topic, g.language, g.template, g.created_at, u.username
            FROM generations g
            LEFT JOIN users u ON u.telegram_id = g.telegram_id
            ORDER BY g.id DESC LIMIT 5
        """).fetchall()

    return {
        "total_users": total_users,
        "total_generations": total_generations,
        "by_language": [dict(r) for r in by_language],
        "top_templates": [dict(r) for r in top_templates],
        "recent": [dict(r) for r in recent],
    }


# ==================== ЗАГОТОВКА ПОД БУДУЩИЕ ПЛАТЕЖИ ====================
# Пока нигде не вызывается — бот бесплатный для всех. Когда подключим
# оплату (Stars/ЮKassa), сюда будет писать соответствующий обработчик.

def record_payment(telegram_id: int, amount: float, currency: str,
                    provider: str, status: str, raw_payload: str = "") -> None:
    with _connect() as conn:
        conn.execute("""
            INSERT INTO payments (telegram_id, amount, currency, provider, status, created_at, raw_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (telegram_id, amount, currency, provider, status, _now(), raw_payload))
        conn.commit()
