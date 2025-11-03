#!/bin/bash
# Скрипт для деплоя Telegram Support Bot на Ubuntu сервер

set -e

PROJECT_DIR="/opt/tg-support-bot"
REPO_URL="https://github.com/sudoloveme/tgsuppbothg.git"
SERVICE_NAME="tg-support-bot"

echo "🚀 Начинаю деплой Telegram Support Bot..."

# Проверка что запущено от root или с sudo
if [ "$EUID" -ne 0 ]; then 
    echo "⚠️  Запустите скрипт с sudo: sudo ./deploy.sh"
    exit 1
fi

# Установка зависимостей системы
echo "📦 Устанавливаю системные зависимости..."
apt-get update
apt-get install -y python3 python3-venv python3-pip git

# Создание директории проекта
if [ ! -d "$PROJECT_DIR" ]; then
    echo "📁 Создаю директорию проекта..."
    mkdir -p "$PROJECT_DIR"
fi

cd "$PROJECT_DIR"

# Клонирование или обновление репозитория
if [ -d ".git" ]; then
    echo "🔄 Обновляю код из GitHub..."
    git pull origin main
else
    echo "📥 Клонирую репозиторий..."
    git clone "$REPO_URL" .
fi

# Создание виртуального окружения
if [ ! -d ".venv" ]; then
    echo "🐍 Создаю виртуальное окружение..."
    python3 -m venv .venv
fi

# Активация venv и установка зависимостей
echo "📚 Устанавливаю Python зависимости..."
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Проверка наличия .env
if [ ! -f ".env" ]; then
    echo "⚠️  Файл .env не найден!"
    echo "Создайте файл .env в директории $PROJECT_DIR"
    echo "Пример содержимого:"
    echo "TELEGRAM_BOT_TOKEN=ваш_токен"
    echo "OWNER_ID=ваш_chat_id"
    echo "или"
    echo "SUPPORT_CHAT_ID=-1001234567890"
    exit 1
fi

# Создание systemd service
echo "⚙️  Настраиваю systemd service..."
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Определяем пользователя для запуска (первый пользователь с /home)
if [ -z "$SERVICE_USER" ]; then
    SERVICE_USER=$(ls -1 /home | head -n 1)
    if [ -z "$SERVICE_USER" ]; then
        SERVICE_USER="root"
    fi
fi

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Telegram Support Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PROJECT_DIR}/.venv/bin/python ${PROJECT_DIR}/bot.py
User=${SERVICE_USER}
Restart=always
RestartSec=5
Environment="PATH=${PROJECT_DIR}/.venv/bin"

[Install]
WantedBy=multi-user.target
EOF

# Перезагрузка systemd и запуск сервиса
echo "🔄 Перезагружаю systemd..."
systemctl daemon-reload

echo "▶️  Запускаю сервис..."
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

# Проверка статуса
sleep 2
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    echo "✅ Бот успешно запущен!"
    echo ""
    echo "Полезные команды:"
    echo "  Просмотр логов: sudo journalctl -u ${SERVICE_NAME} -f"
    echo "  Остановить:    sudo systemctl stop ${SERVICE_NAME}"
    echo "  Запустить:     sudo systemctl start ${SERVICE_NAME}"
    echo "  Перезапустить: sudo systemctl restart ${SERVICE_NAME}"
    echo "  Статус:        sudo systemctl status ${SERVICE_NAME}"
else
    echo "❌ Ошибка запуска сервиса!"
    echo "Проверьте логи: sudo journalctl -u ${SERVICE_NAME} -n 50"
    exit 1
fi

