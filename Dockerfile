FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Режим polling: бот сам опрашивает Telegram, входящий порт/домен не нужен.
CMD ["python3", "bot.py", "polling"]
