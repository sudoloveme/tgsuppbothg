# Настройка HTTPS для Mini-App через Nginx

## Вариант 1: HTTPS на стандартном порту 443 (рекомендуется)

### 1. Установка Nginx и Certbot

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
```

### 2. Настройка Nginx конфигурации

Создайте файл конфигурации:

```bash
sudo nano /etc/nginx/sites-available/miniapp
```

Добавьте следующую конфигурацию:

```nginx
server {
    listen 80;
    server_name your-domain.com;  # Замените на ваш домен

    # Редирект на HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;  # Замените на ваш домен

    # SSL сертификаты (будут добавлены certbot)
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # SSL настройки
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # Проксирование на локальный mini-app сервер
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        
        # Таймауты
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # Увеличенный размер для больших ответов API
    client_max_body_size 10M;
}
```

### 3. Активация конфигурации

```bash
# Создать символическую ссылку
sudo ln -s /etc/nginx/sites-available/miniapp /etc/nginx/sites-enabled/

# Проверить конфигурацию
sudo nginx -t

# Если всё ОК, перезагрузить nginx
sudo systemctl reload nginx
```

### 4. Получение SSL сертификата от Let's Encrypt

```bash
# Убедитесь, что DNS указывает на ваш сервер
# Затем выполните:
sudo certbot --nginx -d your-domain.com

# Certbot автоматически:
# 1. Получит сертификат
# 2. Обновит конфигурацию nginx
# 3. Настроит автообновление сертификата
```

### 5. Обновление .env файла бота

В файле `/opt/tg-support-bot/.env` обновите:

```env
MINIAPP_URL=https://your-domain.com
MINIAPP_PORT=8080
```

### 6. Проверка и перезапуск

```bash
# Проверить статус nginx
sudo systemctl status nginx

# Проверить статус бота
sudo systemctl status tg-support-bot

# Перезапустить бота (если нужно)
sudo systemctl restart tg-support-bot
```

---

## Вариант 2: HTTPS на порту 8080 (нестандартный)

Если вам действительно нужен HTTPS на порту 8080:

### 1. Настройка Nginx

```bash
sudo nano /etc/nginx/sites-available/miniapp
```

```nginx
server {
    listen 8080 ssl http2;
    server_name your-domain.com;  # или IP адрес

    # SSL сертификаты
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # SSL настройки
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    location / {
        proxy_pass http://127.0.0.1:8081;  # Проксируем на другой порт
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 2. Обновить bot.py для использования другого порта

В `bot.py` измените порт mini-app сервера на 8081 (или другой свободный порт).

### 3. Получение сертификата

```bash
# Для нестандартного порта нужно использовать standalone режим
sudo certbot certonly --standalone -d your-domain.com --preferred-challenges http

# Или если у вас уже есть домен на 443, используйте webroot
sudo certbot certonly --webroot -w /var/www/html -d your-domain.com
```

---

## Автоматическое обновление сертификатов

Certbot автоматически настраивает cron задачу для обновления сертификатов. Проверить можно:

```bash
# Проверить автообновление
sudo certbot renew --dry-run

# Проверить когда будет следующее обновление
sudo systemctl status certbot.timer
```

---

## Проверка работы

### 1. Проверить HTTPS

```bash
curl -I https://your-domain.com
```

### 2. Проверить API endpoint

```bash
curl https://your-domain.com/api/subscription/test-uuid
```

### 3. Открыть в браузере

Откройте `https://your-domain.com` в браузере - должна открыться mini-app.

---

## Troubleshooting

### Проблема: "502 Bad Gateway"

**Решение:**
```bash
# Проверить, что mini-app сервер запущен
sudo systemctl status tg-support-bot

# Проверить логи
sudo journalctl -u tg-support-bot -n 50

# Проверить, что порт 8080 слушает
sudo netstat -tlnp | grep 8080
```

### Проблема: "SSL certificate problem"

**Решение:**
```bash
# Проверить сертификат
sudo certbot certificates

# Обновить сертификат
sudo certbot renew
```

### Проблема: Nginx не стартует

**Решение:**
```bash
# Проверить конфигурацию
sudo nginx -t

# Проверить логи
sudo tail -f /var/log/nginx/error.log
```

---

## Безопасность

### 1. Firewall

```bash
# Разрешить только HTTPS (порт 443)
sudo ufw allow 443/tcp
sudo ufw allow 80/tcp  # Для редиректа на HTTPS
sudo ufw deny 8080/tcp  # Закрыть прямой доступ к порту 8080
```

### 2. Ограничение доступа к API (опционально)

Если хотите ограничить доступ к API только для Telegram:

```nginx
location /api/ {
    # Проверка заголовка User-Agent (Telegram WebApp)
    if ($http_user_agent !~* "TelegramBot") {
        return 403;
    }
    
    proxy_pass http://127.0.0.1:8080;
    # ... остальные proxy настройки
}
```

---

## Пример полной конфигурации с безопасностью

```nginx
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    # SSL
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Proxy settings
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    client_max_body_size 10M;
}
```

---

## Быстрый старт (скрипт)

```bash
#!/bin/bash
# Установка и настройка nginx для mini-app

DOMAIN="your-domain.com"  # Замените на ваш домен

# Установка
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx

# Создание конфигурации
sudo tee /etc/nginx/sites-available/miniapp > /dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    return 301 https://\$server_name\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name $DOMAIN;

    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

# Активация
sudo ln -sf /etc/nginx/sites-available/miniapp /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# Получение сертификата
sudo certbot --nginx -d $DOMAIN

# Firewall
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw deny 8080/tcp

echo "Готово! Обновите MINIAPP_URL в .env на https://$DOMAIN"
```

