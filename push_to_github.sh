#!/bin/bash
set -e

echo "1) Инициализация git..."
git init

echo "2) Добавление файлов..."
git add .

echo "3) Коммит..."
git commit -m "clean start" || echo "   (нечего коммитить — возможно, уже закоммичено, продолжаем)"

echo "4) Переключение на ветку main..."
git branch -M main

echo "5) Настройка адреса GitHub-репозитория..."
if git remote | grep -q "^origin$"; then
  git remote set-url origin https://github.com/thegleb2005-ui/pptx-bot.git
else
  git remote add origin https://github.com/thegleb2005-ui/pptx-bot.git
fi

echo "6) Заливаем на GitHub (может попросить логин/токен)..."
git push -u origin main --force

echo ""
echo "✅ Готово! Открой https://github.com/thegleb2005-ui/pptx-bot и проверь файлы."
