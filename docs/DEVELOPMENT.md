# Локальная разработка и проверки

## Локальный запуск

Нужны Python 3.12 и Docker Compose v2 (или собственный PostgreSQL).

```bash
cp .env.example .env
# Задайте DJANGO_SECRET_KEY в .env, например с помощью:
python3.12 -c 'import secrets; print(secrets.token_urlsafe(48))'

docker compose up -d db
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Откройте http://127.0.0.1:8000/admin/ и добавьте SearchQuery:
имя, source=Avito, enabled и URL поиска, скопированный из Avito с нужными фильтрами.
Для fast scan рекомендуется сортировка «по дате» (`s=104`).

`.env.example` задаёт `COMPOSE_FILE=docker-compose.yml:docker-compose.local.yml`:
локальный override публикует PostgreSQL только на 127.0.0.1.
Если 5432 занят, измените POSTGRES_PORT, например на 55432.
С собственной PostgreSQL Docker не требуется; укажите её параметры в .env.
Используйте отдельную dev-БД, никогда не направляйте локальные тесты в production.

```bash
python manage.py collect_listings --source avito --mode fast --headed
python manage.py collect_listings --source avito --mode fast --headed --wait-for-captcha 300
# Full отключён по умолчанию; включать только после проверки стабильности доступа:
AVITO_FULL_SCAN_ENABLED=true python manage.py collect_listings --source avito --mode full --search-id 1
```

Без `--headed` используется headless; также есть явный `--headless`.
Браузерный транспорт открывает один browser и обходит enabled-поиски последовательно.
Avito также поддерживает Mobile API; выбор задаёт [polling-профиль](POLLING_METHODS.md).
PostgreSQL advisory lock защищает ручные одновременные запуски внутри одного источника.
После завершения Chromium закрывается. Для запуска через назначенный polling
профиль Chromium-профиль и состояние запросов сохраняются в
`AVITO_STATE_DIR/profiles/<profile-code>/`; это не смешивает cookies разных
маршрутов. При ручном создании collector без профиля сохраняется legacy-путь
`AVITO_STATE_DIR/chromium`.
По умолчанию это `.avito-state/` в каталоге проекта; каталог исключён из Git.

### Сессия, ручная капча и паузы

- `AVITO_PAGE_DELAY_SECONDS=120`: минимальный интервал между поисковыми переходами,
  включая разные поиски и последовательные запуски. Это экспериментальный интервал,
  а не установленный лимит Авито; он не ограничивает фоновые запросы самого браузера.
- `AVITO_BLOCK_COOLDOWN_SECONDS=3600`: после HTTP 403/429/439 или страницы блокировки
  сбор прекращается для всех оставшихся поисков. Следующий запуск не отправляет запросы
  до истечения паузы. Если `Retry-After` требует ждать дольше, используется его срок.
- `AVITO_FULL_SCAN_ENABLED=false`: полные обходы отключены по умолчанию.
  Fast по умолчанию читает одну страницу. Расписание Avito fast — примерно каждые четыре часа
  с 08:15 до 20:15 по timezone сервера, со случайной задержкой до 10 минут.
- `--headed --wait-for-captcha 300`: при блокировке оставляет окно на пять минут для
  ручного прохождения капчи. Сборщик читает уже открытую выдачу без повторного перехода,
  затем сохраняет объявления обычным способом. Капчу он автоматически не решает.
  Для этого режима нужен графический экран; в Docker — настроенный X/VNC.
  Действующую паузу между запусками этот флаг не отменяет.

Состояние пауз хранится в `request-state.json`, диагностика последнего ответа — в
`last-response.json` внутри `AVITO_STATE_DIR`: время UTC, URL, HTTP-код, заголовок
страницы, количество карточек, `Retry-After` и `Content-Type`. Cookies в диагностику
не записываются. После ручного восстановления HTTP-код равен `null`, поскольку
читается текущий DOM, а новый запрос не выполняется. Сохранение `storage_state()`
в цикле не используется. Не запускайте два браузера с одним профилем одновременно.

При обновлении старой установки явно замените `AVITO_PAGE_DELAY_SECONDS=2` в `.env`
на `120`: существующие значения окружения имеют приоритет над новыми defaults.

Проверьте Listing, PriceHistory, ListingSnapshot и Scan в Admin.
Повторный неизменившийся scan обновляет last_seen_at без дублей истории.
Отсутствующая в карточке цена сохраняет последнюю известную.
Фото не скачиваются, хранится только URL.

Fast scan никогда не деактивирует объявления. Full scan идёт до пустой страницы.
MAX_PAGES и серия страниц без новых ID означают **partial/failed**, а не полный
обход; собранные данные сохраняются, отсутствующие не деактивируются.
Повреждённые карточки пропускаются; если они встретились в full scan,
деактивация также запрещена. Listing активен, пока активна хотя бы одна его связь
с поиском. Отключение SearchQuery само по себе не меняет active-state.
Изменение фильтров URL существующего поиска применяется при следующем полном обходе.

Неизвестный title сохраняется с nullable характеристиками и warning.
Студии и другие неописанные в ТЗ форматы также сохраняются без разбора характеристик.
published_text информационный; время обнаружения — first_seen_at.
При отсутствии отдельного описания сохраняется текст карточки без относительной даты,
чтобы дата не создавала ложные snapshots.
Блокировка, HTTP-ошибка или неизвестная пустая страница дают failed Scan и ненулевой
код command. Поведение cookies и SPFA описано в [инструкции доступа](AUTHORIZATION_CIAN_AVITO.md).

## Проверки перед завершением изменения

```bash
venv/bin/python manage.py check
venv/bin/python manage.py makemigrations --check --dry-run
venv/bin/pytest
```

Тесты требуют отдельной PostgreSQL БД и права CREATEDB. Не используйте production-настройки. Необязательные браузерные проверки запускаются отдельно:

```bash
RUN_BROWSER_TESTS=1 venv/bin/pytest collectors/tests/test_browser_manual.py
```

Правила изменения кода: [AGENTS.md](../AGENTS.md). Дополнительные ограничения: [collectors](../collectors/AGENTS.md), [services](../listings/services/AGENTS.md).
