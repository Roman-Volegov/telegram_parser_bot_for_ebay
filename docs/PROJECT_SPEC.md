# Спецификация проекта DecoParser / ebay-poshmark-bot

**Репозиторий:** `roman-volegov/telegram_parser_bot_for_ebay`  
**Рабочая ветка:** `cursor/telegram-bot-scaffold-2601`  
**Продакшен (VPS):** Docker Compose + Caddy HTTPS (`PUBLIC_BASE_URL` / `MINIAPP_DOMAIN`)  
**Бренд Mini App:** DECOTIC / DecoParser  

Документ фиксирует **текущее состояние** продукта и **согласованные доработки** из рабочей переписки (оптимизации, multi-source, категории по источникам).

---

## 1. Назначение

Мультипользовательский Telegram-бот и Mini App для мониторинга новых объявлений на маркетплейсах:

- eBay (официальный Browse API и/или HTML/RSS-парсер)
- Poshmark (HTML-парсер)
- Etsy (Open API v3 и/или Playwright-браузер с постоянным профилем)

Пользователь создаёт поиски по ключевым словам и фильтрам; фоновый поллер находит новые лоты и присылает карточки в Telegram.

---

## 2. Цели продукта

1. Быстро узнавать о новых лотах по заданным запросам.
2. Работать с несколькими площадками из одного интерфейса.
3. Не требовать от пользователя хранения секретов в чате (ключи — в Mini App, шифрование).
4. Масштабироваться на нескольких пользователей с модерацией доступа админом.
5. Устойчиво работать на одном VPS рядом с другими сервисами (3x-ui и т.п.).

---

## 3. Роли

| Роль | Возможности |
|------|-------------|
| Гость | `/start`, заявка на доступ |
| Пользователь (`approved`) | настройка источников, ключи, поиски, уведомления, Mini App |
| Админ | `/users`, approve/reject/block; ссылки на CAPTCHA Etsy (noVNC) |

Статусы пользователя: `pending` → `approved` / `rejected` / `blocked`.

---

## 4. Выжимка текущего состояния (as-is)

### 4.1. Стек

| Слой | Технологии |
|------|------------|
| Язык | Python 3.12 |
| Бот | aiogram 3, FSM (Redis или memory) |
| Web API / Mini App | FastAPI + uvicorn, статика `webapp/` |
| БД | SQLite (aiosqlite, WAL) |
| Кэш / FSM | Redis 8 |
| Скрапинг | httpx, BeautifulSoup/lxml, feedparser, Playwright |
| CAPTCHA | Xvfb + x11vnc + websockify/noVNC |
| Edge | Caddy 2 (TLS, ACME, forward_auth) |
| Деплой | Docker Compose: `bot`, `redis`, `caddy` |

### 4.2. Структура репозитория

```
bot/                 # приложение
  main.py            # polling + uvicorn
  config.py, db.py, models.py, crypto.py, cards.py
  handlers/          # start, admin, setup, searches, menu
  providers/         # ebay_api, ebay_parser, poshmark, etsy
  services/          # poller, cleanup, credentials, etsy_access
  web/               # Mini App API, deletion, noVNC auth
webapp/              # Mini App + landing
tests/               # pytest
docker-compose.yml, Caddyfile, Dockerfile
.github/workflows/ci.yml
```

### 4.3. Источники (провайдеры)

| Source | Механизм | Ключи |
|--------|----------|-------|
| `ebay_api` | eBay Browse API + OAuth client credentials | Client ID / Secret на пользователя |
| `ebay_parser` | HTML-поиск, fallback RSS | не нужны |
| `poshmark` | HTML публичной выдачи | не нужны |
| `etsy` | API v3 при наличии ключа, иначе Playwright | опционально API key |

Общий контракт: `BaseProvider.search(search, limit) → list[Listing]`.

### 4.4. Модель данных (SQLite)

- **users** — telegram_id, статус, setup_completed, enabled_sources, ebay_marketplace, deletion token.
- **credentials** — Fernet-шифрование ключей eBay/Etsy, AAD = telegram_id.
- **searches** — одна строка на источник; мульти-поиск Mini App связывается через `group_key`. Поля: keywords, min/max price, condition, buy_it_now, paused, `filters_json`.
- **seen_items** — `(search_id, item_id)` — дедуп **на строку поиска**, не на группу.
- **poll_logs** — статус последнего цикла по `(telegram_id, search_id)`.

`filters_json` сейчас фактически хранит регион eBay (`marketplace`: `EBAY_US` и др.). Категорий маркетплейсов **нет**.

### 4.5. Группировка поисков

Один логический поиск в Mini App = несколько строк `searches` с общим `group_key` (по одной на источник).  
В списке API/UI — одна карточка с `sources[]`.  
Бот `/add` по-прежнему создаёт **один** источник за раз.

### 4.6. Поллер

- Интервал: `POLL_INTERVAL_SEC` (по умолчанию 300 с).
- Параллелизм по источникам (семафоры): Etsy 1, eBay API 5, eBay parser 4, Poshmark 4.
- Первый прогон по пустому `seen` — тихий **seed** (без уведомлений).
- `seen` пишется **только после успешной** отправки карточки.
- Лимит: до 8 новых карточек на поиск за цикл; остальное ждёт следующий цикл.
- Cleanup `seen_items`: TTL (по умолчанию 90 дней), только для **paused** поисков.

### 4.7. Bot vs Mini App

**Бот:** доступ, админка, команды `/add` `/list` `/edit` `/pause` `/resume` `/delete`, карточки лотов, редирект настройки в Mini App.

**Mini App** (вкладки: Поиски / Новый / Лог / Настройки):

- старт всегда на «Поиски»;
- мульти-источники в create/edit;
- цена, регион eBay и Buy It Now (поля eBay — только если выбран eBay);
- **нет** UI категорий и **нет** UI `condition` (condition есть в API/боте).

Авторизация API: Telegram `initData` (HMAC), заголовок `X-Telegram-Init-Data`.

### 4.8. Безопасность и инфраструктура

- AccessMiddleware: публичный `/start`, админ-команды, остальное — только `approved`.
- Секреты eBay только через Mini App; Fernet + AAD.
- eBay Marketplace Account Deletion: `/ebay/deletion/{telegram_id}/{route_token}`.
- Etsy CAPTCHA: одноразовые signed tickets → HttpOnly cookie; Caddy `forward_auth`; TTL 12 ч; порты noVNC только на localhost.
- Caddy на 443; ACME webroot для `XUI_ACME_HOST` (совместимость с продлением сертификата 3x-ui).
- Порт приложения `18080` и noVNC `16080` не торчат наружу.

### 4.9. Что уже сделано в рамках чата (оптимизации и UI)

- Надёжные уведомления (seen после send; перенос лишних лотов).
- SQLite WAL / busy_timeout; безопасная очистка seen.
- Параллельный poller, кэши credentials/shipping/HTML, reuse HTTP-провайдеров.
- Группировка поисков (`group_key`), multi-source Mini App.
- CAPTCHA tickets + forward_auth, TTL 12 ч.
- Redis FSM, healthcheck, лимиты памяти, CI, pinned deps.
- Стиль Mini App Decotic; eBay-поля при edit с eBay в группе.
- Caddy → 443; сохранение ACME для 3x-ui через webroot.

### 4.10. Известные ограничения (as-is)

- Poshmark зависит от вёрстки HTML; полный taxonomy API отсутствует — каталог собирается с публичных URL + seed.
- Etsy без API часто упирается в DataDome → нужен браузер + ручная CAPTCHA.
- Ценовой фильтр eBay API при границах цены завязан на `priceCurrency:USD`.
- Seen не общий на группу: один лот может прийти отдельно с каждой площадки.
- Без `REDIS_URL` FSM в памяти (теряется при рестарте).
- Полные деревья eBay/Etsy подтягиваются по кнопке «Обновить каталоги» (нужны ключи); до этого работают seed-каталоги.

### 4.11. Категории (реализовано)

- Отдельная страница «Категории» из create/edit; блоки по источникам; несколько категорий на источник (OR); typeahead + дерево.
- Пустой список = поиск по всем категориям.
- Кэш таксономий: TTL 30 дней + кнопка в Настройках `POST /api/categories/refresh`.
- `filters_json.categories[]` per source; провайдеры прокидывают category в запросы.

---

## 5. Функциональные требования

### 5.1. Реализовано

| ID | Требование | Статус |
|----|------------|--------|
| F1 | Заявка на доступ и модерация админом | ✅ |
| F2 | Выбор источников и хранение ключей | ✅ |
| F3 | CRUD поисков (бот + Mini App) | ✅ |
| F4 | Один поиск = несколько источников (Mini App) | ✅ |
| F5 | Фильтры: keywords, min/max price, BIN, регион eBay | ✅ |
| F6 | Уведомления о новых лотах карточками | ✅ |
| F7 | Лог последнего цикла поллера в Mini App | ✅ |
| F8 | Etsy CAPTCHA через защищённый noVNC | ✅ |
| F9 | eBay deletion endpoint | ✅ |

### 5.2. Согласовано к реализации (из чата)

| ID | Требование | Статус |
|----|------------|--------|
| F10 | Отдельные категории **для каждого** выбранного источника в одном поиске | ✅ |
| F11 | Загрузка каталога категорий (seed + refresh с площадок) | ✅ |
| F12 | Поля выбора категорий на отдельной странице только для выбранных источников | ✅ |
| F13 | Категория необязательна (пусто = поиск только по keywords/цене) | ✅ |
| F14 | Несколько категорий на источник (OR), typeahead + дерево | ✅ |
| F15 | Автообновление каталогов раз в месяц + кнопка в Настройках | ✅ |

**Не делать (явно):** бэкап БД в рамках прошлых оптимизаций; общая «универсальная» категория с маппингом на все площадки.

---

## 6. ADR: категории по источникам (целевая доработка)

### 6.1. Цель
Показывать и сохранять отдельные категории для каждого источника; подгружать полные каталоги площадок; в формах создания/редактирования показывать выбор категорий только у выбранных источников.

### 6.2. Краткое описание архитектурного решения
В одном поиске пользователь отмечает площадки. Под каждой отмеченной площадкой появляется свой выбор категории из полного каталога этой площадки. Категория хранится отдельно для eBay, Etsy и Poshmark и влияет только на опрос соответствующей площадки.

### 6.3. За рамками ADR
- Категории в текстовом FSM бота (только Mini App на первом этапе).
- Автоподбор категории по ключевым словам.
- Единая категория на все источники.
- Изменение логики цен/BIN/регионов (кроме соседства полей в UI).

### 6.4. Описание архитектурного решения

**Хранение:** в `filters_json` строки источника:

- eBay: `category_id`, опционально `category_path`
- Etsy: `taxonomy_id`, опционально `category_path`
- Poshmark: `department` / `category` / `subcategory` (+ path для UI)

**API:**

- расширение `POST/PATCH /api/searches` полем категорий по источникам;
- `GET /api/categories?source=…&marketplace=…` (+ опционально `parent_id` для ленивого дерева);
- кэш таксономий (диск/SQLite/Redis, TTL ~24ч).

**Источники каталогов:**

| Площадка | Источник данных |
|----------|-----------------|
| Etsy | Seller taxonomy nodes API |
| eBay | Commerce Taxonomy (`getDefaultCategoryTreeId` + `getCategoryTree`) по marketplace |
| Poshmark | нет публичного taxonomy API → справочник по URL-структуре (department/category/subcategory) с кэшем; риск неполноты |

**UI:** динамические блоки категорий; каскад/поиск по дереву для eBay/Etsy; 2–3 уровня для Poshmark.

**Провайдеры:** прокинуть category в Browse filter / `_sacat` / Etsy `taxonomy_id` / Poshmark URL params.

**Reseed:** при смене категории — тихий reseed только затронутых строк группы.

**Порядок внедрения:** схема+API → кэш каталогов → провайдеры → Mini App → тесты → деплой VPS.

### 6.5. Риски
Большие деревья eBay/Etsy без кэша тормозят UI; Poshmark-каталог может устаревать; смена деревьев площадок ломает старые ID (нужен мягкий fallback).

### 6.6. Альтернативы
Общий маппинг категорий; короткий curated-список; ручной ввод ID без дерева — отклонены как не соответствующие требованиям F10–F12.

---

## 7. Нефункциональные требования

| ID | Требование |
|----|------------|
| N1 | Один VPS, изоляция портов (приложение и noVNC только localhost) |
| N2 | HTTPS для Mini App и deletion URL |
| N3 | Секреты не логировать; CAPTCHA tickets не светить в access logs |
| N4 | CI: ruff, pytest, pip-audit, `docker compose config` |
| N5 | Устойчивость SQLite при параллельном поллере (WAL, busy_timeout) |
| N6 | Ограничение нагрузки на площадки семафорами поллера |
| N7 | Совместимость Caddy с продлением LE-сертификата 3x-ui (ACME webroot) |

---

## 8. API (Mini App) — контур

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/me` | профиль / статус / источники |
| POST | `/api/setup` | завершение настройки |
| GET/POST/PATCH/DELETE | `/api/searches` | поиски (группы) |
| GET | `/api/poll-logs` | лог цикла |
| DELETE | `/api/keys`, `/api/keys/etsy` | отзыв ключей |
| GET/POST | `/ebay/deletion/{id}/{token}` | eBay compliance |
| — | `/api/categories` | **планируется** (F10–F12) |

Health: `/health` (внутренний).

---

## 9. Конфигурация (ключевые переменные)

`TELEGRAM_BOT_TOKEN`, `ADMIN_TELEGRAM_IDS`, `CREDENTIALS_ENCRYPTION_KEY`,  
`PUBLIC_BASE_URL`, `MINIAPP_DOMAIN`, `XUI_ACME_HOST`,  
`POLL_INTERVAL_SEC`, `SEEN_ITEMS_TTL_DAYS`, `DATABASE_PATH`,  
`REDIS_URL`, `ETSY_NOVNC_TOKEN`, `ETSY_NOVNC_TTL_SEC`,  
`ETSY_BROWSER_*`, `HTTP_PROXY`, `LOG_LEVEL`.

Полный шаблон: `.env.example`.

---

## 10. Деплой

```text
VPS → docker compose up -d --build
  bot   (ebay-poshmark-bot)  + volume данных / Chromium profile
  redis (ebay-poshmark-redis)
  caddy (ebay-poshmark-caddy) :80/:443
```

Публично: HTTPS Mini App `/app/`, landing `/`.  
Внутри: `127.0.0.1:18080`, `127.0.0.1:16080`.

---

## 11. Тестирование

- Локально / CI: `ruff`, `pytest`, `pip-audit`.
- Покрыто: db/groups, poller, crypto, telegram auth, deletion, middleware, API models, провайдеры (helpers).
- Для F10–F12 потребуется: тесты записи категорий в `filters_json`, API categories cache, URL/params провайдеров, условная логика UI (по возможности e2e/контракт).

---

## 12. Roadmap (ближайший)

1. Реализация категорий по источникам (раздел 6).
2. Уточнение стратегии Poshmark-каталога (полный scrape-справочник vs ограниченная глубина URL).
3. По необходимости: категории в боте FSM; UI `condition` в Mini App; общий seen на группу — **не согласованы**, вне текущего скоупа.

---

## 13. Глоссарий

| Термин | Значение |
|--------|----------|
| Поиск (логический) | Карточка в Mini App; может включать несколько источников |
| Строка поиска | Запись в `searches` на один Source |
| `group_key` | Связь строк одного логического поиска |
| Seed | Первый тихий прогон без уведомлений |
| Taxonomy / category | Категория каталога площадки (ещё не в продукте) |

---

*Документ составлен по коду репозитория и рабочей переписке агента (оптимизации, multi-source Mini App, инфраструктура Caddy/3x-ui, план категорий F10–F12).*
