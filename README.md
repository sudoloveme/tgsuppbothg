## Telegram Support Bot

Минимальный бот поддержки: все сообщения от пользователей пересылаются владельцу (вашему аккаунту). Отвечайте владельцу реплаем — ответ уйдёт пользователю.

### 1) Установка

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

### 2) Настройка окружения

Создайте файл `.env` в корне проекта со значениями:

```
TELEGRAM_BOT_TOKEN=123456:ABC-Your-Bot-Token
# Ваш личный Telegram user ID (chat id). Узнать: отправьте /id в ЛС боту
OWNER_ID=123456789
## (опционально) Режим форума: ID супергруппы с включёнными темами
# Пример: -1001234567890
SUPPORT_CHAT_ID=-1001234567890
## (опционально) Путь к SQLite БД (по умолчанию data.db в корне)
DB_PATH=/opt/tg-support-bot/data.db
```

Как получить OWNER_ID:
- Запустите бота (см. ниже) и в ЛС боту отправьте `/id` — бот ответит числом. Это и есть ваш chat_id.

### 3) Запуск

```bash
python bot.py
```

Бот использует long polling. Остановить: Ctrl+C.

### 4) Как это работает

- Вариант А (по умолчанию, `OWNER_ID`): Пользователь пишет боту — бот копирует сообщение вам (владельцу). Ответьте реплаем — бот отправит ответ пользователю.
- Вариант Б (форум, `SUPPORT_CHAT_ID`): Для каждого пользователя создаётся отдельная тема (topic) в супергруппе. Сообщения клиента публикуются в его тему; отвечайте реплаем в нужной теме — бот доставит ответ пользователю.

Персистентность:
- Маппинг пользователь → topic сохраняется в SQLite (`DB_PATH`), переживает рестарт/деплой.

Поддерживаются текст и медиа (копированием сообщения), форматирование сохранится.

### 5) Команды

- `/start` — приветствие для пользователя; подсказка для владельца.
- `/id` — показать текущий chat_id (используйте, чтобы узнать OWNER_ID).

### 6) Деплой на Ubuntu сервер

#### Быстрый деплой (автоматический)

1. Подключитесь к серверу:
```bash
ssh ubuntu@IP_СЕРВЕРА
```

2. Скачайте и запустите скрипт деплоя:
```bash
curl -O https://raw.githubusercontent.com/sudoloveme/tgsuppbothg/main/deploy.sh
chmod +x deploy.sh
sudo ./deploy.sh
```

3. Создайте файл `.env` в `/opt/tg-support-bot/`:
```bash
sudo nano /opt/tg-support-bot/.env
```
Добавьте:
```
TELEGRAM_BOT_TOKEN=ваш_токен
OWNER_ID=ваш_chat_id
# или для форума:
# SUPPORT_CHAT_ID=-1001234567890
```

4. Перезапустите сервис:
```bash
sudo systemctl restart tg-support-bot
```

#### Ручной деплой

```bash
# На сервере
sudo mkdir -p /opt/tg-support-bot
cd /opt/tg-support-bot
sudo git clone https://github.com/sudoloveme/tgsuppbothg.git .

# Создать venv и установить зависимости
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt

# Создать .env файл
sudo nano .env

# Создать systemd service (см. пример в deploy.sh)

# Запустить
sudo systemctl enable tg-support-bot
sudo systemctl start tg-support-bot
```

#### Полезные команды для управления

```bash
# Просмотр логов
sudo journalctl -u tg-support-bot -f

# Статус сервиса
sudo systemctl status tg-support-bot

# Перезапуск после обновления кода
cd /opt/tg-support-bot
sudo git pull
sudo systemctl restart tg-support-bot

# Остановка/запуск
sudo systemctl stop tg-support-bot
sudo systemctl start tg-support-bot
```

### 7) Примечания

- Соответствия сообщений хранятся в памяти и теряются при перезапуске.
- Для продакшна можно добавить БД или Webhook, но для начала достаточно polling.
- Чтобы включить форум в группе: Превратите группу в супергруппу и включите "Темы" в настройках. Затем добавьте бота с правом создавать темы.


