# Home Hunter

Личный мониторинг квартир: Django Admin, PostgreSQL, обычные HTML-страницы
Avito через Playwright Chromium. Python 3.12. Redis, Celery и API не нужны.

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
Один command открывает один browser и обходит enabled-поиски последовательно.
PostgreSQL advisory lock защищает и ручные одновременные запуски.
После завершения Chromium закрывается, профиль сохраняется в `AVITO_STATE_DIR/chromium`.
По умолчанию это `.avito-state/` в каталоге проекта; каталог исключён из Git.

### Сессия, ручная капча и паузы

- `AVITO_PAGE_DELAY_SECONDS=120`: минимальный интервал между поисковыми переходами,
  включая разные поиски и последовательные запуски. Это экспериментальный интервал,
  а не установленный лимит Авито; он не ограничивает фоновые запросы самого браузера.
- `AVITO_BLOCK_COOLDOWN_SECONDS=3600`: после HTTP 403/429/439 или страницы блокировки
  сбор прекращается для всех оставшихся поисков. Следующий запуск не отправляет запросы
  до истечения паузы. Если `Retry-After` требует ждать дольше, используется его срок.
- `AVITO_FULL_SCAN_ENABLED=false`: полные обходы отключены по умолчанию.
  Fast по умолчанию читает одну страницу. Существующее расписание fast — раз в час.
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
код command. Автоматического обхода антибота нет.

## Тесты

```bash
pytest
python manage.py makemigrations --check --dry-run
python manage.py migrate --check
python manage.py check
# Необязательно: настоящий Chromium на локальной fixture, без обращений к Avito
RUN_BROWSER_TESTS=1 pytest collectors/tests/test_browser_manual.py
```

Тесты используют отдельную `test_<POSTGRES_DB>` PostgreSQL БД.
Роли нужен CREATEDB; локальный пользователь из Compose уже имеет это право.
Парсер проверяется HTML fixtures, collector — подменой загрузки страниц.
Live Avito проверяется вручную указанными выше командами, а не обычным pytest.

## Production: Ubuntu 24.04, /opt/home_hunter

Установите Docker Engine с Compose plugin, разместите проект в /opt/home_hunter.
Создайте .env из примера и измените:
- `COMPOSE_FILE=docker-compose.yml` (без local override);
- `DJANGO_DEBUG=false`;
- `DJANGO_SECRET_KEY` — длинный случайный секрет;
- `DJANGO_ALLOWED_HOSTS=SERVER_IP` — реальный IP;
- `POSTGRES_DB=home_hunter`, POSTGRES_USER и сильный POSTGRES_PASSWORD;
- `POSTGRES_HOST=db`, `POSTGRES_PORT=5432`.

Права .env: `chmod 600 .env`. Секреты не входят в Docker image и git.
Compose принудительно выставляет DEBUG=false и адрес db внутри контейнеров.

```bash
cd /opt/home_hunter
docker compose config --quiet
docker compose build
# Collector в profile, поэтому собирается отдельно:
docker compose build collector
docker compose up -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py check
docker compose ps
```

Постоянно работают только db, web, caddy. Caddy публикует порт 80,
web:8000 и PostgreSQL:5432 наружу не опубликованы.
Разрешите firewall только 80/tcp и 22/tcp для SSH.
Откройте http://SERVER_IP/admin/. Данные защищены стандартным Django login.
Admin static files собираются при build и раздаются WhiteNoise через web.

```bash
docker compose run --rm collector python manage.py collect_listings --source avito --mode fast
# Только для явно разрешённого полного обхода:
docker compose run --rm -e AVITO_FULL_SCAN_ENABLED=true collector python manage.py collect_listings --source avito --mode full
```

Docker volume `avito_state` сохраняет профиль, паузы и диагностику в `/app/.avito-state`
между одноразовыми контейнерами collector. В image каталог принадлежит пользователю app.

Collector существует только до завершения команды. Для обновления: соберите оба
image, выполните migrate и `docker compose up -d`. Не используйте
`docker compose down -v`, если данные должны сохраниться.
Регулярно сохраняйте PostgreSQL backup вне VPS, например:
```bash
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > home_hunter.sql
```

Когда появится домен, замените deploy/Caddyfile:
```caddy
homehunter.example.com {
    reverse_proxy web:8000
}
```
Добавьте публикацию 443:443 в caddy и разрешите 443/tcp в firewall,
настройте DNS, перезапустите Caddy. Его volumes сохраняют сертификаты.

### Расписание и логи

Проверьте путь к docker (`command -v docker`); units используют /usr/bin/docker.
Оба service используют общий /tmp/home-hunter-collector.lock через flock.
Код 75 означает пропуск занятого lock и считается нормальным завершением.

```bash
sudo cp deploy/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now home-hunter-avito-fast.timer
# На существующем сервере временно отключите полный обход:
sudo systemctl disable --now home-hunter-avito-full.timer
systemctl list-timers
systemctl status home-hunter-avito-fast.service
journalctl -u home-hunter-avito-fast.service
journalctl -u home-hunter-avito-full.service
docker compose logs web caddy
```

Fast — hourly с разбросом до 5 минут. Full — 03:30 по timezone сервера
1, 4, 7… числа каждого месяца (примерно каждые 3 дня, интервал на границе месяца
может быть короче). Persistent запускает пропущенное срабатывание после включения.
Если lock занят, scan пропускается до следующего запуска.

### Swap

Для VPS с 2 GB RAM рекомендуется swap 2 GB против случайного OOM Chromium.
Если swap ещё не настроен:
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### Проверка deployment

```bash
docker compose config --quiet
docker compose up -d
docker compose ps --services --status running
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec web python manage.py migrate --check
docker compose exec web python manage.py check
docker compose exec web sh -c '! command -v chromium && ! command -v chromium-browser && test ! -d /ms-playwright'
docker compose run --rm collector python manage.py collect_listings --source avito --mode fast
curl -I http://SERVER_IP/admin/
```

Web image не устанавливает Playwright и Chromium. Collector использует одну browser
instance на command (Chromium имеет внутренние дочерние процессы).
Архитектура расширяется через BaseCollector и registry; CIAN в MVP не реализован.

Дополнительная Docker-проверка без live-запросов к Avito (на отдельной dev-БД):
```bash
docker compose run --rm -T collector python manage.py shell < collectors/tests/docker_smoke.py
```
Она запускает настоящий Chromium с перехватом URL и HTML fixture, выполняет fast/full,
проверяет историю в PostgreSQL и удаляет созданные тестовые записи.
