# Telegram-бот для eBay и Poshmark

Бот ищет лоты на eBay / Poshmark и присылает уведомления о новых совпадениях по подпискам.

## Возможности

- `/search ebay|poshmark <запрос> [max=цена]` — разовый поиск
- `/watch ebay|poshmark <запрос> [min=цена] [max=цена]` — подписка на новые лоты
- `/watches` / `/unwatch <id>` — управление подписками
- фоновый опрос выдачи и уведомления в Telegram
- SQLite для подписок и уже виденных лотов
- опциональный eBay Browse API; без ключей — HTML-поиск

## Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполните TELEGRAM_BOT_TOKEN в .env
python -m bot
```

## Переменные окружения

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен от @BotFather |
| `ALLOWED_USER_IDS` | опциональный allowlist user id |
| `WATCH_POLL_INTERVAL_SECONDS` | интервал опроса (по умолчанию 120) |
| `EBAY_APP_ID` / `EBAY_CERT_ID` | ключи eBay API (опционально) |
| `DATABASE_PATH` | путь к SQLite |

## Архитектура

```
bot/
  main.py          # запуск polling + watcher
  handlers.py      # команды Telegram
  watcher.py       # фоновый мониторинг
  parsers/         # eBay и Poshmark
  db.py            # SQLite
  formatting.py    # тексты сообщений
```

## Замечания

- Poshmark официального публичного API не даёт: используется разбор HTML, вёрстка может меняться.
- Первый прогон подписки только запоминает текущую выдачу, без спама старыми лотами.
- Для стабильного eBay лучше подключить Browse API.
