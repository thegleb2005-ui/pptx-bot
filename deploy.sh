#!/bin/bash
# deploy.sh — сборка и загрузка образа бота в Yandex Cloud
# Запуск на Маке:  bash deploy.sh
set -e

FOLDER_ID="b1gafmjpa3r2qs61i466"
REGISTRY_NAME="pptx-bot-registry"
IMAGE_NAME="pptx-bot"

echo "== Шаг 1/4: ищу или создаю реестр контейнеров =="
REGISTRY_ID=$(yc container registry list --folder-id "$FOLDER_ID" --format json | python3 -c "import sys,json;d=json.load(sys.stdin);print(d[0]['id'] if d else '')")
if [ -z "$REGISTRY_ID" ]; then
    REGISTRY_ID=$(yc container registry create --folder-id "$FOLDER_ID" --name "$REGISTRY_NAME" --format json | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
    echo "Реестр создан: $REGISTRY_ID"
else
    echo "Реестр найден: $REGISTRY_ID"
fi

IMAGE_TAG="cr.yandex/$REGISTRY_ID/$IMAGE_NAME:latest"

echo "== Шаг 2/4: настраиваю доступ Docker к Яндексу =="
yc container registry configure-docker

echo "== Шаг 3/4: собираю образ (важно: под linux/amd64, т.к. у Мака другой процессор) =="
docker build --platform linux/amd64 -t "$IMAGE_TAG" .

echo "== Шаг 4/4: загружаю образ в облако (может занять пару минут) =="
docker push "$IMAGE_TAG"

echo ""
echo "======================================================"
echo "✅ ГОТОВО! Образ загружен:"
echo ""
echo "   $IMAGE_TAG"
echo ""
echo "Скопируй эту строку — она понадобится при создании"
echo "контейнера в консоли Яндекс Клауда (см. инструкцию)."
echo "======================================================"
