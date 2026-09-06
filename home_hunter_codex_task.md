# Home Hunter — техническое задание для Codex

## 1. Цель проекта

Реализовать с нуля небольшой личный сервис **Home Hunter** для мониторинга объявлений о продаже квартир перед покупкой недвижимости.

Основные задачи сервиса:

- периодически собирать объявления о продаже квартир;
- первым источником реализовать **Avito**;
- сохранять объявления в PostgreSQL;
- отслеживать появление новых объявлений;
- отслеживать изменение цены;
- хранить историю изменения объявления;
- отслеживать исчезновение объявлений;
- позволять вручную запускать сбор локально на тестовой БД;
- позволять развернуть сервис на небольшом VPS;
- заложить архитектуру, в которую позднее можно добавить **CIAN** и другие источники без переделки основной бизнес-логики.

Приложение предназначено для одного пользователя и должно быть максимально простым по инфраструктуре.

---

# 2. Ограничения по ресурсам

Production VPS:

- Ubuntu 24.04 предпочтительно;
- 1 vCPU;
- 2 GB RAM;
- 40 GB disk.

Также потенциально возможен Debian 11 / 1 vCPU / 2 GB / 20 GB, поэтому приложение не должно требовать тяжёлой постоянной инфраструктуры.

Нельзя добавлять без необходимости:

- Redis;
- Celery;
- RabbitMQ;
- Elasticsearch;
- отдельный SPA frontend;
- постоянно работающий Chromium;
- несколько параллельных collector-процессов.

Основной принцип: в idle-состоянии сервис должен потреблять минимум памяти.

---

# 3. Технологический стек

Использовать:

- Python 3.12;
- Django;
- PostgreSQL;
- Playwright + Chromium для Avito collector;
- Gunicorn для production web;
- Caddy как reverse proxy;
- Docker / Docker Compose;
- pytest;
- pytest-django.

На первом этапе интерфейс реализовать через:

- Django Admin;
- простые Django templates при необходимости.

DRF добавлять только если реально понадобится API. На MVP он не нужен.

---

# 4. Общая production-архитектура

Постоянно работают только три контейнера:

```text
Internet
   |
   v
Caddy :80
   |
   v
Django + Gunicorn
   |
   v
PostgreSQL
```

Отдельно на уровне host OS работают `systemd timers`.

Они периодически запускают временный collector-container:

```text
systemd timer
    |
    v
systemd service
    |
    v
docker compose run --rm collector ...
    |
    v
Django management command
    |
    v
Playwright + Chromium
    |
    v
Avito
    |
    v
PostgreSQL
    |
    v
container exits and is removed
```

Collector **не должен работать постоянно**.

---

# 5. Caddy

На первом этапе домена нет.

Приложение должно быть доступно по публичному IP:

```text
http://SERVER_IP
```

Первоначальный Caddyfile:

```caddy
:80 {
    reverse_proxy web:8000
}
```

Наружу публиковать только:

- `80/tcp`;
- `22/tcp` для SSH.

Не публиковать:

- PostgreSQL `5432`;
- Gunicorn `8000`.

Когда позднее появится домен, архитектура должна позволять заменить Caddyfile на:

```caddy
homehunter.example.com {
    reverse_proxy web:8000
}
```

и получить автоматический HTTPS средствами Caddy.

---

# 6. Режимы запуска

Проект должен одинаково хорошо поддерживать локальную разработку и production.

## 6.1. Local development

PostgreSQL запускается через Docker:

```bash
docker compose up -d db
```

Django запускается из локального virtualenv:

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Приложение:

```text
http://127.0.0.1:8000
```

Collector также запускается вручную из virtualenv:

```bash
python manage.py collect_listings \
    --source avito \
    --mode fast \
    --headed
```

или:

```bash
python manage.py collect_listings \
    --source avito \
    --mode full \
    --headed
```

Локальный Playwright должен использовать установленный Chromium:

```bash
playwright install chromium
```

Важно:

- Django не должен требовать Docker для local development;
- Docker нужен только для PostgreSQL, если разработчик не использует локальный PostgreSQL;
- данные local development должны сохраняться в отдельную локальную test/dev БД.

---

## 6.2. Production

Production запускается:

```bash
docker compose up -d
```

После этого постоянно работают:

```text
db
web
caddy
```

Collector объявлен в compose, но не стартует автоматически.

Ручной production-запуск:

```bash
docker compose run --rm collector \
    python manage.py collect_listings \
    --source avito \
    --mode fast
```

Full scan:

```bash
docker compose run --rm collector \
    python manage.py collect_listings \
    --source avito \
    --mode full
```

---

# 7. Docker

Подготовить `docker-compose.yml`.

Services:

```text
db
web
caddy
collector
```

Collector должен использовать compose profile или иной механизм, чтобы:

```bash
docker compose up -d
```

не запускал его постоянно.

Рекомендуется два Docker image target.

## Web image

Содержит:

- Python;
- Django;
- Gunicorn;
- psycopg;
- application code.

Не содержит:

- Chromium;
- Playwright system dependencies.

## Collector image

Содержит:

- тот же application code;
- Django;
- psycopg;
- Playwright;
- Chromium;
- необходимые Playwright system dependencies.

Это нужно, чтобы web-container оставался лёгким.

---

# 8. Production Gunicorn

VPS имеет только 1 vCPU и приложение предназначено для одного пользователя.

Использовать скромную конфигурацию:

```bash
gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 1 \
    --threads 2 \
    --timeout 60
```

Не добавлять большое количество workers.

---

# 9. PostgreSQL

Использовать PostgreSQL.

Database credentials только через environment variables.

Создать:

```text
.env.example
```

Не коммитить реальные secrets.

Пример переменных:

```env
DJANGO_SECRET_KEY=
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost

POSTGRES_DB=home_hunter
POSTGRES_USER=home_hunter
POSTGRES_PASSWORD=home_hunter
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432

AVITO_FAST_SCAN_PAGES=1
AVITO_MAX_PAGES=50
AVITO_NO_NEW_PAGES_LIMIT=2
AVITO_PAGE_DELAY_SECONDS=2
```

Для Docker production `POSTGRES_HOST` должен быть `db`.

Не выставлять агрессивные PostgreSQL memory settings.

---

# 10. Swap

В README добавить рекомендацию для VPS с 2 GB RAM создать swap 2 GB.

Это не часть приложения, но нужно задокументировать как production recommendation для защиты от случайного OOM Chromium.

---

# 11. Структура проекта

Не дробить проект чрезмерно.

Предпочтительно:

```text
home_hunter/
├── config/
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
│
├── listings/
│   ├── migrations/
│   ├── models.py
│   ├── admin.py
│   ├── services/
│   │   ├── persistence.py
│   │   └── scans.py
│   └── tests/
│
├── collectors/
│   ├── base.py
│   ├── registry.py
│   │
│   ├── avito/
│   │   ├── collector.py
│   │   ├── parser.py
│   │   ├── selectors.py
│   │   └── types.py
│   │
│   └── tests/
│
├── management/
│   └── commands/
│       └── collect_listings.py
│
├── templates/
├── docker/
│   ├── web.Dockerfile
│   └── collector.Dockerfile
│
├── deploy/
│   ├── Caddyfile
│   └── systemd/
│       ├── home-hunter-avito-fast.service
│       ├── home-hunter-avito-fast.timer
│       ├── home-hunter-avito-full.service
│       └── home-hunter-avito-full.timer
│
├── docker-compose.yml
├── .env.example
├── requirements.txt / pyproject.toml
├── manage.py
└── README.md
```

Если удобнее использовать единый Dockerfile с targets `web` и `collector`, это допустимо.

---

# 12. Django models

## 12.1. SearchQuery

Создать модель поискового запроса.

```python
class SearchQuery(models.Model):
    class Source(models.TextChoices):
        AVITO = "avito", "Avito"
        CIAN = "cian", "CIAN"

    name = models.CharField(max_length=255)

    source = models.CharField(
        max_length=20,
        choices=Source.choices,
    )

    url = models.TextField()

    enabled = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

Через Django Admin пользователь должен иметь возможность добавить несколько поисков.

URL пользователь формирует вручную через интерфейс Avito с нужными фильтрами.

Для fast monitoring рекомендуется URL с:

```text
s=104
```

то есть сортировкой Avito «по дате».

Collector должен обходить все `enabled=True` SearchQuery указанного source.

---

## 12.2. Listing

Основная модель объявления.

Минимальные поля:

```text
source
external_id
url
title
description
price
price_per_sqm
rooms
area
floor
floors_total
address
district
published_text
image_url
first_seen_at
last_seen_at
is_active
created_at
updated_at
```

Рекомендуемые типы:

- `price` — BigIntegerField nullable;
- `price_per_sqm` — BigIntegerField nullable;
- `area` — DecimalField nullable;
- integer characteristics nullable;
- `url`, `image_url` — TextField либо URLField;
- description — TextField.

Constraint:

```text
(source, external_id) UNIQUE
```

Добавить разумные индексы:

```text
(source, external_id)
price
rooms
area
is_active
first_seen_at
last_seen_at
```

---

## 12.3. ListingSearchQuery

Промежуточная модель между Listing и SearchQuery.

Поля:

```text
listing
search_query
first_seen_at
last_seen_at
is_active
```

Constraint:

```text
(listing, search_query) UNIQUE
```

---

## 12.4. PriceHistory

Поля:

```text
listing
price
observed_at
```

Правила:

- запись создаётся при первом обнаружении Listing;
- новая запись создаётся только при фактическом изменении цены;
- не создавать историю цены при каждом scan;
- в `Listing.price` хранить последнюю известную цену.

---

## 12.5. ListingSnapshot

Поля:

```text
listing
observed_at
data JSONField
```

Snapshot создавать:

- при первом обнаружении объявления;
- при значимом изменении объявления.

Не создавать snapshot при каждом scan.

Значимые поля MVP:

```text
price
title
description
address
district
rooms
area
floor
floors_total
```

---

## 12.6. Scan

Модель истории запуска collector.

Поля:

```text
search_query
source
mode
started_at
finished_at
pages_scanned
items_seen
unique_items_seen
new_items
updated_items
price_changes
status
error
```

`mode`:

```text
fast
full
```

`status`:

```text
running
success
failed
```

При исключении Scan должен сохраняться как `failed`, а в `error` — краткое описание исключения.

---

# 13. Collector abstraction

Основная application logic не должна зависеть от конкретного сайта.

Создать интерфейс:

```python
class BaseCollector(ABC):

    @abstractmethod
    async def collect(
        self,
        search: SearchQuery,
        *,
        mode: str,
    ) -> CollectionResult:
        ...
```

Реализовать:

```python
class AvitoCollector(BaseCollector):
    ...
```

Архитектура должна позволять позднее добавить:

```python
class CianCollector(BaseCollector):
    ...
```

без изменения моделей и persistence logic.

---

# 14. NormalizedListing DTO

Collector не должен напрямую создавать Django model instances.

Создать DTO:

```python
@dataclass
class NormalizedListing:
    source: str
    external_id: str
    url: str
    title: str
    price: int | None
    price_per_sqm: int | None
    rooms: int | None
    area: Decimal | None
    floor: int | None
    floors_total: int | None
    address: str | None
    district: str | None
    description: str | None
    published_text: str | None
    image_url: str | None
    raw_data: dict
```

Parser Avito возвращает только такие структуры.

Persistence layer ничего не должен знать о Playwright/DOM.

---

# 15. Avito collector — подтверждённый способ работы

Для первой версии использовать только обычную HTML search page Avito через Playwright.

НЕ использовать:

```text
SPFA
curl_cffi
mobile API
/api/11/items
/web/1/js/items
proxy
bypass API
```

В предварительных экспериментах подтверждено:

- обычная поисковая страница Avito успешно открывается Playwright Chromium;
- прямой API request даёт антибот `403/439`;
- DOM поисковой страницы содержит необходимую базовую информацию;
- пагинация работает.

---

# 16. Avito selectors

Основная карточка:

```css
[data-marker="item"]
```

External ID:

```html
data-item-id
```

Не использовать autogenerated CSS classes.

Использовать только:

- `data-marker`;
- `data-item-id`;
- `itemprop`;
- семантические HTML attributes.

Основные selectors:

```css
[data-marker="item"]
[data-marker="item-title"]
[data-marker="item-price"]
[data-marker="item-price"] meta[itemprop="price"]
[data-marker="item-address"]
[data-marker="item-date"]
[itemprop="image"]
```

Selectors вынести в `selectors.py`.

---

# 17. Парсинг Avito карточки

Для каждой карточки получить:

```text
external_id
title
url
price
price_per_sqm если доступно
address
district если можно надёжно выделить
published_text
image_url
description / raw visible text
```

Цена предпочтительно берётся из:

```css
[data-marker="item-price"] meta[itemprop="price"]
```

URL:

- взять `href` у title;
- привести к absolute URL через `https://www.avito.ru`.

---

# 18. Title parser

Поддержать как минимум:

```text
2-к. квартира, 58,6 м², 6/12 эт.
```

и:

```text
Аукцион: 3-к. квартира, 72,9 м², 7/9 эт.
```

Из title извлекать:

```text
rooms
area
floor
floors_total
```

Regex должен искать pattern внутри строки.

Если формат неизвестен:

- collector не падает;
- исходный title сохраняется;
- непонятные поля становятся `None`;
- warning в logger.

---

# 19. published_text

Возможные значения:

```text
1 час назад
Вчера
3 дня назад
7 дней назад
1 неделю назад
None
```

Не использовать это поле как primary source времени появления.

Использовать `first_seen_at`.

---

# 20. Avito pagination

Подтверждено:

```text
p=1
p=2
p=3
...
```

Наблюдаемое количество карточек:

```text
p=1   около 60
p>=2  около 50
```

Количество не фиксировать в коде.

Обязательно дедуплицировать `(source, external_id)` и IDs внутри одного scan.

---

# 21. Avito sorting

Сортировка «по дате»:

```text
s=104
```

Подтверждённое поведение:

```text
page 1 -> сейчас / вчера
page 2 -> примерно 3 дня
page 3 -> примерно 7 дней
page 4-5 -> около недели
```

Строгий chronological ordering внутри страницы не гарантирован.

Fast scan должен основываться на external_id, а не на parsed relative date.

---

# 22. Fast scan

Настройка:

```text
AVITO_FAST_SCAN_PAGES=1
```

Алгоритм:

1. открыть search URL;
2. собрать N первых страниц;
3. дедуплицировать external_id;
4. новые объявления сохранить;
5. существующим обновить `last_seen_at`;
6. обнаружить изменение цены;
7. создать PriceHistory только при изменении;
8. создать Snapshot при значимом изменении;
9. обновить ListingSearchQuery.

Fast scan **никогда не помечает объявления inactive**.

---

# 23. Full scan

Алгоритм:

```text
page = 1

while page <= MAX_PAGES:
    load page

    if no listings:
        stop

    process listings
    page += 1
```

Настройки:

```text
AVITO_MAX_PAGES=50
AVITO_NO_NEW_PAGES_LIMIT=2
```

Не вычислять pages count из отображаемого общего количества.

Если подряд N страниц не дают новых IDs относительно текущего scan, остановиться с warning.

---

# 24. Исчезнувшие объявления

Только успешный full scan может менять active-state.

Для SearchQuery:

- увиденные -> `ListingSearchQuery.is_active=True`;
- ранее связанные, но не увиденные -> `False`.

`Listing.is_active=True`, если существует хотя бы одна активная relation.

Failed/partial scan не должен ничего деактивировать.

---

# 25. Persistence service

Создать service:

```python
process_listing(
    search_query,
    normalized_listing,
    observed_at,
)
```

Он должен атомарно:

1. найти/создать Listing;
2. создать initial PriceHistory для нового;
3. создать initial Snapshot;
4. создать/update ListingSearchQuery;
5. сравнить старое состояние;
6. создать PriceHistory при изменении цены;
7. создать Snapshot при значимом изменении;
8. обновить Listing;
9. обновить `last_seen_at`;
10. вернуть structured result.

Использовать `transaction.atomic()` там, где это оправдано.

---

# 26. Management command

Создать:

```bash
python manage.py collect_listings
```

Параметры:

```text
--source avito
--mode fast|full
--search-id ID
--headed
--headless
```

Примеры:

```bash
python manage.py collect_listings --source avito --mode fast
```

```bash
python manage.py collect_listings --source avito --mode full --search-id 1
```

```bash
python manage.py collect_listings --source avito --mode fast --headed
```

Production default: `headless=True`.

---

# 27. Browser lifecycle

В рамках одного command:

- запустить один Playwright;
- один Chromium;
- последовательно обработать SearchQuery;
- не запускать searches параллельно;
- не запускать Chromium на каждую страницу;
- закрыть browser;
- завершить command.

---

# 28. Delay между страницами

Настройка:

```text
AVITO_PAGE_DELAY_SECONDS=2
```

Применять в full scan.

Не делать aggressive crawling.

---

# 29. Error handling

Повреждение одной карточки не должно ронять scan.

Ошибка page/browser/block — должна.

Если Avito показывает:

```text
Доступ ограничен
проверка безопасности
проблема с IP
```

считать Scan failed.

Не реализовывать автоматический anti-bot bypass на MVP.

---

# 30. Scheduling через systemd

Периодический опрос запускает **host OS**, а collector container существует только на время выполнения.

## Fast scan

Создать:

```text
deploy/systemd/home-hunter-avito-fast.service
deploy/systemd/home-hunter-avito-fast.timer
```

Service:

```bash
cd /opt/home_hunter

flock -n /tmp/home-hunter-collector.lock docker compose run --rm collector python manage.py collect_listings     --source avito     --mode fast
```

Timer: примерно каждый час.

Например:

```ini
[Timer]
OnCalendar=hourly
Persistent=true
```

## Full scan

Создать:

```text
home-hunter-avito-full.service
home-hunter-avito-full.timer
```

Запускать примерно раз в 3 дня ночью, например около 03:30.

---

# 31. Locking

Fast и full service используют общий:

```text
/tmp/home-hunter-collector.lock
```

через:

```bash
flock -n
```

Если один scan уже идёт, второй не запускается.

На VPS нельзя допускать два Chromium одновременно.

---

# 32. systemd logging

Должны работать:

```bash
systemctl status home-hunter-avito-fast.service
journalctl -u home-hunter-avito-fast.service
journalctl -u home-hunter-avito-full.service
systemctl list-timers
```

README должен это описывать.

---

# 33. Django Admin

Настроить Admin для:

```text
SearchQuery
Listing
ListingSearchQuery
PriceHistory
ListingSnapshot
Scan
```

Listing:

- search по title/address/external_id;
- filters source, rooms, district, is_active;
- current price;
- first_seen_at/last_seen_at.

SearchQuery:

- name;
- source;
- enabled;
- URL.

Scan:

- status;
- mode;
- source;
- search;
- started/finished;
- pages/items/new/price changes;
- error.

PriceHistory желательно inline в Listing.

---

# 34. Доступ и authentication

Сервис доступен по публичному IP.

Не оставлять данные публичными.

На MVP использовать стандартную Django authentication.

Главная страница может редиректить на login.

Admin защищён стандартной authentication.

Production:

```text
DEBUG=False
```

`ALLOWED_HOSTS` через env.

---

# 35. Фотографии

Не скачивать изображения.

Хранить только `image_url`.

---

# 36. Logging

Стандартный Django/Python logging.

Логировать:

```text
scan start
search name
page number
items count
new listings
updated listings
price changes
scan finished
scan failed
```

Не писать полный HTML в production logs.

---

# 37. Тесты

Обязательные unit tests.

## Title parser

Проверить стандартный title, `Аукцион:` и unknown format.

## Persistence

Проверить:

- создание нового Listing;
- initial PriceHistory;
- initial Snapshot;
- повтор без изменений;
- изменение цены;
- изменение description;
- один Listing в нескольких SearchQuery.

## Full scan state

Проверить:

- успешный scan может деактивировать relation;
- failed scan не деактивирует;
- Listing остаётся active, пока активен хотя бы в одном SearchQuery.

## Parser tests

Не ходить live на Avito.

Использовать HTML fixtures.

Playwright live integration test сделать optional/manual.

---

# 38. README

README должен описывать реальный end-to-end запуск.

## Local

```bash
cp .env.example .env

docker compose up -d db

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
playwright install chromium

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Добавление SearchQuery через Admin.

Ручной fast/full scan.

## Production

```bash
docker compose build
docker compose up -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

Collector:

```bash
docker compose run --rm collector     python manage.py collect_listings     --source avito     --mode fast
```

systemd installation:

```bash
sudo cp deploy/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now home-hunter-avito-fast.timer
sudo systemctl enable --now home-hunter-avito-full.timer
```

---

# 39. Не делать на MVP

Не реализовывать:

- CIAN collector;
- Telegram;
- ML/LLM;
- Property cross-source deduplication;
- SPA;
- maps;
- geocoding;
- Redis/Celery;
- WebSockets;
- proxy rotation;
- anti-bot bypass;
- image download;
- отдельный analytics warehouse.

Но архитектура должна позволять добавить CIAN позднее.

---

# 40. Code quality

Требования:

- type hints;
- небольшие функции;
- разделение collector/parser/persistence;
- никаких God classes;
- selectors отдельно;
- settings через env;
- dataclasses/DTO;
- migrations в commit;
- минимум скрытой магии.

---

# 41. Acceptance criteria

## Local

Работает:

```bash
docker compose up -d db
python manage.py migrate
python manage.py runserver
```

Через Admin создаётся SearchQuery.

Работает:

```bash
python manage.py collect_listings     --source avito     --mode fast     --headed
```

и объявления появляются в local PostgreSQL.

## Avito

Collector умеет:

- открыть search URL;
- получить `[data-marker="item"]`;
- получить `data-item-id`;
- price;
- title;
- URL;
- address;
- description/raw text;
- `published_text=None`;
- pagination;
- deduplication.

## Persistence

Повторный scan:

- не создаёт duplicate Listing;
- обновляет last_seen_at;
- PriceHistory только при изменении;
- Snapshot только при изменении.

## Production

```bash
docker compose up -d
```

запускает только:

```text
db
web
caddy
```

Collector не работает постоянно.

```bash
docker compose run --rm collector ...
```

успешно запускается и завершается.

Caddy отдаёт Django через:

```text
http://SERVER_IP
```

## Scheduling

Есть готовые systemd units.

Fast scan — hourly.

Full scan — примерно раз в 3 дня ночью.

Оба используют общий `flock`.

---

# 42. Порядок реализации

1. Django project + settings/env.
2. PostgreSQL + models + migrations + Admin.
3. NormalizedListing + BaseCollector.
4. Avito parser + fixtures/tests.
5. Persistence service + tests.
6. Playwright AvitoCollector.
7. Fast/full scan + Scan lifecycle.
8. Management command.
9. Local end-to-end verification.
10. Docker images + Compose.
11. Caddy.
12. systemd timers/services + locking.
13. README.
14. Final test and deployment validation.

---

# 43. Финальная проверка Codex

Перед завершением задачи обязательно:

1. запустить весь test suite;
2. проверить migrations;
3. проверить `python manage.py check`;
4. проверить `docker compose config`;
5. убедиться, что `docker compose up -d` не поднимает collector;
6. проверить local workflow;
7. проверить production commands в README;
8. убедиться, что web image не содержит Chromium;
9. убедиться, что collector запускает только один Chromium;
10. вывести краткий отчёт:
   - что реализовано;
   - какие файлы созданы;
   - как запустить локально;
   - как вручную проверить Avito collector;
   - как развернуть production.
