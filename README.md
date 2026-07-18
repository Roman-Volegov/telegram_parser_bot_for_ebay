# Telegram-бот мониторинга eBay + Poshmark

Мультипользовательский бот: заявки на доступ, мастер настройки источников, зашифрованные eBay API ключи, поиски и карточки новых лотов.

## Возможности

- Доступ: `pending → approved/rejected/blocked`, заявки админу
- Источники: **eBay API**, **eBay Parser** (RSS + HTML), **Poshmark**
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
cp .env.example .env   # или отредактировать существующий .env
# обязательны: TELEGRAM_BOT_TOKEN, ADMIN_TELEGRAM_IDS, CREDENTIALS_ENCRYPTION_KEY
# PUBLIC_BASE_URL=http://<VPS_IP>:18080
sudo docker compose up -d --build
sudo docker compose logs -f
```

Хостовый порт webhook: **18080**. Контейнер: `ebay-poshmark-bot`.

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
- Для Production eBay keyset укажите deletion URL и verification token из `/setup`.
