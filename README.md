# Telegram-бот мониторинга eBay + Poshmark + Etsy

Мультипользовательский бот: заявки на доступ, мастер настройки источников, зашифрованные eBay API ключи, поиски и карточки новых лотов.

## Возможности

- Доступ: `pending → approved/rejected/blocked`, заявки админу
- Источники: **eBay API**, **eBay Parser** (RSS + HTML), **Poshmark**, **Etsy**
- `/setup` — выбор источников, OAuth-проверка ключей, deletion URL
- Поиски: `/add` `/list` `/edit` `/pause` `/resume` `/delete`
- Карточки: фото, цена, описание, URL-кнопка
- Поллер (`POLL_INTERVAL_SEC`, по умолчанию 5 мин), тихий первый прогон
- Cleanup `seen_items` старше 90 дней
- FastAPI: `GET/POST /ebay/deletion/{telegram_id}`

## Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Сгенерируйте ключ шифрования:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Заполните в `.env`:

- `TELEGRAM_BOT_TOKEN`
- `ADMIN_TELEGRAM_IDS` (через запятую)
- `CREDENTIALS_ENCRYPTION_KEY`
- `PUBLIC_BASE_URL` (публичный HTTPS URL этого сервиса)

Запуск (бот + webhook):

```bash
python -m bot
```

## Docker на VPS (рядом с другим ботом)

Отдельный каталог и compose-проект — не пересекается с `~/cdek-bot`.

```bash
cd ~/ebay-poshmark-bot
cp .env.example .env
# TELEGRAM_BOT_TOKEN, ADMIN_TELEGRAM_IDS, CREDENTIALS_ENCRYPTION_KEY
# PUBLIC_BASE_URL=https://<IP>.sslip.io:8443
# MINIAPP_DOMAIN=<IP>.sslip.io
sudo docker compose up -d --build
```

- HTTP health: `18080`
- HTTPS Mini App (Caddy): `8443` → `/app/`
- Public landing page (English): `https://<IP>.sslip.io:8443/`
- Контейнеры: `ebay-poshmark-bot`, `ebay-poshmark-caddy`

### Ручная проверка Etsy DataDome

Etsy работает в постоянном Chromium-профиле. Укажите случайные значения
`ETSY_NOVNC_PASSWORD` и `ETSY_NOVNC_TOKEN` в `.env`. Когда DataDome покажет
CAPTCHA, бот отправит пользователю кнопку с защищённой HTTPS-ссылкой. Она
открывает текущий браузер и автоматически подключается к нему, в том числе
с телефона.

Публичный путь содержит длинный секретный token. Не пересылайте ссылку:
получивший её сможет управлять окном Etsy. Прямой порт noVNC остаётся доступен
только локально на VPS. Для резервного подключения создайте SSH-туннель:

```bash
ssh -L 6080:127.0.0.1:16080 deploy@<VPS_IP>
```

Откройте `http://127.0.0.1:6080/vnc.html`, нажмите **Connect**, введите пароль
и вручную пройдите CAPTCHA Etsy. Профиль и cookie сохраняются в Docker volume.

### Mini App
Открывается кнопкой **📱 Mini App**, командой `/app` или menu button бота.  
Авторизация через Telegram `initData` (HMAC).

## Команды

**Пользователь:** `/start` `/setup` `/settings` `/add` `/list` `/edit` `/pause` `/resume` `/delete` `/keys_status` `/revoke_keys` `/help`

**Админ:** `/users` `/approve` `/reject` `/block`

## Структура

```
bot/
  main.py           # polling + uvicorn
  config.py
  db.py / crypto.py / models.py
  handlers/         # start, admin, setup, searches
  providers/        # ebay_api, ebay_parser, poshmark
  services/         # poller, cleanup, credentials
  web/              # eBay deletion endpoint
```

## Замечания

- Секреты eBay шифруются Fernet; ciphertext привязан к `telegram_id` (AAD).
- Сообщения с Client Secret удаляются из чата.
- Poshmark — HTML-парсер публичной выдачи (вёрстка может меняться).
- Etsy — **Playwright (Chromium)** с постоянным профилем; первичная проверка
  DataDome проходится вручную через защищённый SSH-туннелем noVNC.
  Опционально Open API v3 (`keystring:shared_secret`) в Mini App, шифруется как eBay.
- Для Production eBay keyset укажите deletion URL и verification token из `/setup`.
