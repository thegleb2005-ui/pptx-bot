#!/bin/bash
set -e

echo "0) Проверяю config.py на наличие старого токена..."
if grep -q "AAFUMcPAnhieYL" config.py 2>/dev/null; then
  echo ""
  echo "❌ СТОП: в config.py всё ещё старый захардкоженный токен."
  echo "   Замени файл config.py на новый (без хардкода) и запусти скрипт заново."
  echo ""
  exit 1
fi
echo "   config.py чистый, продолжаем."

echo "1) Удаляю старую git-историю полностью..."
rm -rf .git

echo "2) Удаляю все папки __pycache__..."
find . -name "__pycache__" -type d -not -path "./venv/*" -exec rm -rf {} + 2>/dev/null || true

echo "3) Инициализация новой git-истории..."
git init

echo "4) Добавление файлов..."
git add .

echo "5) Коммит..."
git commit -m "clean start"

echo "6) Переключение на ветку main..."
git branch -M main

echo "7) Настройка адреса GitHub-репозитория..."
git remote add origin https://github.com/thegleb2005-ui/pptx-bot.git 2>/dev/null || \
  git remote set-url origin https://github.com/thegleb2005-ui/pptx-bot.git

echo "8) Заливаем на GitHub (может попросить логин/токен)..."
git push -u origin main --force

echo ""
echo "✅ Готово! Открой https://github.com/thegleb2005-ui/pptx-bot и проверь файлы."
