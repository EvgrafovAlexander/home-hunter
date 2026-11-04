# Развёртывание и эксплуатация

Инструкция для конфигурации в репозитории. Фактически включённые процессы проверяйте через systemctl и Docker; наличие unit-файла не означает его включение.

## Production: Ubuntu 24.04, /opt/home_hunter

Установите Docker Engine с Compose plugin, разместите проект в /opt/home_hunter.
Создайте .env из примера и измените:
- `COMPOSE_FILE=docker-compose.yml` (без local override);
- `DJANGO_DEBUG=false`;
- `DJANGO_SECRET_KEY` — длинный случайный секрет;
- `DJANGO_ALLOWED_HOSTS=SERVER_IP` — реальный IP;
- `POSTGRES_DB=home_hunter`, POSTGRES_USER и сильный POSTGRES_PASSWORD;
- `POSTGRES_HOST=db`, `POSTGRES_PORT=5432`.
- `SHARED_SSH_DESTINATION=user@host` — обязательный адрес SSH-сервера туннеля;
- `PUBLIC_BASE_URL` — внешний URL приложения для ссылок в уведомлениях.

Адреса конкретной установки хранятся только в `.env`. Без непустой
`SHARED_SSH_DESTINATION` Compose завершает проверку с ошибкой.

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

Полное описание контейнеров, Compose profiles и systemd-процессов находится в
[docs/DOCKER_ARCHITECTURE.md](DOCKER_ARCHITECTURE.md).

Постоянно работают `db`, `web`, `caddy` и `telegram-poll`. Для опросов CIAN,
Avito и DomClick отдельно запускаются профильные сервисы; общий SSH SOCKS
`shared-socks-tunnel` включается профилем `collector`, `cian` или `domclick`.
Caddy публикует порт 80, `web:8000` и PostgreSQL:5432 наружу не опубликованы.
Разрешите firewall только 80/tcp и 22/tcp для SSH.
Откройте http://SERVER_IP/admin/. Данные защищены стандартным Django login.
Admin static files собираются при build и раздаются WhiteNoise через web.

```bash
docker compose --profile collector run --rm collector
# Только для явно разрешённого полного обхода:
docker compose --profile collector run --rm -e AVITO_FULL_SCAN_ENABLED=true collector xvfb-run -a python manage.py collect_listings --source avito --mode full --headed
```

Docker volume `avito_proxy_state` сохраняет паузы и диагностику в `/app/.avito-state`
между одноразовыми контейнерами collector. В image каталог принадлежит пользователю app.

Данные PostgreSQL находятся в именованном volume `postgres_data`, поэтому остановка,
перезапуск или пересоздание контейнера `db` их не удаляет. При штатной остановке Compose
даёт PostgreSQL до 60 секунд на checkpoint; после аварийного завершения PostgreSQL
восстанавливается из WAL при следующем запуске. Volume удаляется только явной командой
`docker compose down -v` (или `docker volume rm`), поэтому для защиты от потери VPS
нужна копия `pg_dump` вне сервера.

Collector существует только до завершения команды. Для обновления: соберите оба
image, выполните migrate и `docker compose up -d`. Не используйте
`docker compose down -v`, если данные должны сохраниться.
Регулярно сохраняйте PostgreSQL backup вне VPS, например:
```bash
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > home_hunter.sql
```

Для ручной выгрузки через интерфейс используйте SSH-туннель (домен не нужен):
```bash
ssh -L 8080:127.0.0.1:80 root@SERVER_IP
```
Добавьте `127.0.0.1,localhost` в `DJANGO_ALLOWED_HOSTS` production `.env`, затем
перезапустите web. После этого откройте `http://127.0.0.1:8080/admin/backups/`, войдите staff-пользователем
и нажмите «Создать и скачать backup». Файл `.dump` скачивается на ноутбук и удаляется
из временной директории сервера после передачи. Не открывайте этот раздел через обычный
публичный HTTP: backup содержит всю базу HomeHunter.

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
Avito и CIAN используют разные flock-файлы (`/tmp/home-hunter-avito.lock` и
`/tmp/home-hunter-cian.lock`), поэтому опрос одного источника не блокирует другой.
Код 75 означает пропуск занятого lock внутри одного источника и считается нормальным завершением.

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

Avito fast — примерно каждые четыре часа с 08:15 до 20:15 по timezone сервера,
со случайной задержкой до 10 минут.
Full выключен; его шаблон — 03:30 по timezone сервера
1, 4, 7… числа каждого месяца (примерно каждые 3 дня, интервал на границе месяца
может быть короче). Persistent запускает пропущенное срабатывание после включения.
Если lock занят, scan пропускается до следующего запуска.

### Геокодирование карты

После настройки `NOMINATIM_USER_AGENT` в production `.env` включите ограниченный
таймер: он обрабатывает до 40 новых адресов в час и делает не более одного запроса
в секунду к публичному Nominatim.

```bash
sudo cp deploy/systemd/home-hunter-geocoding.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now home-hunter-geocoding.timer
systemctl list-timers home-hunter-geocoding.timer
```

### Определение микрорайона по полигонам

Каталог полигонов Уфы хранится в `data/ufa_microdistrict_polygons.json` и может
обновляться публичным источником:

```bash
python scripts/extract_ufa_microdistrict_polygons.py
```

Для предварительной проверки объявлений с уже сохранёнными координатами:

```bash
python manage.py resolve_microdistricts --source avito --only-missing --dry-run
```

После проверки результата примените изменения, убрав `--dry-run`. Ручные
исправления объявлений команда не перезаписывает.

### Уведомления об опросах

Задайте `TG_BOT_TOKEN` и `TG_CHAT_ID` в production `.env`. При необходимости
укажите `TG_CHANNEL_ID`, чтобы публиковать уведомления в Telegram-канал (бот
должен быть администратором канала; для закрытого канала используйте его chat ID,
обычно вида `-100...`). Для единого потока новых видимых объявлений включите
`TG_LISTING_ALERTS_ENABLED=true`: каждое объявление будет опубликовано один раз
с кратким описанием, оценкой и причинами; выгодные варианты получат отдельную
пометку. В сообщении адрес и оценка выделяются, а причины разделяются на плюсы,
минусы и информационные факторы. Для Telegram отдельно запустите `docker compose --profile collector up -d shared-socks-tunnel`: обычный `up -d` не включает профиль туннеля.

Сообщения Telegram отправляются через внутренний
SOCKS-туннель `shared-socks-tunnel`; для этого укажите
`TELEGRAM_PROXY_URL=socks5://shared-socks-tunnel:1080`. Таймер проверяет устаревание
данных раз в час, а после каждого scan состояние оценивается сразу.

Изменения цены при включённом `TG_PRICE_ALERTS_ENABLED=true` отправляются только
при изменении от `TG_PRICE_ALERT_MIN_PERCENT` процентов (по умолчанию 3%):
снижения — в канал, повышения — только в `TG_CHAT_ID`.

Публикация новых объявлений выполняется отдельным постоянным Telegram-воркером.
Он проверяет очередь раз в 10 минут, отправляет до 10 объявлений за проход и
повторяет неудачные отправки с увеличивающейся задержкой. Объявление сначала
проходит тот же фильтр, что и обычная лента; при отсутствии координат оно ждёт
первой попытки геокодирования. Для включения на стенде установите сервис:

```bash
sudo cp deploy/systemd/home-hunter-telegram-send.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now home-hunter-telegram-send.service
sudo systemctl status home-hunter-telegram-send.service
```

Состояние каждой публикации и последняя ошибка доступны в Django Admin в разделе
`Telegram listing alerts`.

```bash
sudo cp deploy/systemd/home-hunter-scan-health.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now home-hunter-scan-health.timer
```

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
docker compose --profile collector run --rm collector
curl -I http://SERVER_IP/admin/
```

Web image не устанавливает Playwright и Chromium. Collector использует одну browser
instance на command (Chromium имеет внутренние дочерние процессы).
ЦИАН реализован через BaseCollector и registry. Подробное описание и эксплуатация:
[Сбор ЦИАН и туннель между VPS](CIAN.md).

Дополнительная Docker-проверка без live-запросов к Avito (на отдельной dev-БД):
```bash
docker compose run --rm -T collector python manage.py shell < collectors/tests/docker_smoke.py
```
Она запускает настоящий Chromium с перехватом URL и HTML fixture, выполняет fast/full,
проверяет историю в PostgreSQL и удаляет созданные тестовые записи.

### Восстановление Telegram-публикаций

Worker повторно забирает `sending` с истёкшим `locked_until` (или без срока).
Обычный срок захвата — две минуты; восстановление происходит при следующем
опросе очереди, по умолчанию раз в десять минут. Перед отправкой worker проверяет
номер попытки и продлевает действующий захват. Уже сохранённые `sent` повторно
не выбираются, в том числе при ошибке последующего обновления оценки.

При аварии после принятия сообщения Telegram, но до сохранения `sent`, возможен
дубль: локальная БД не знает результата внешнего запроса. Длительная пауза
процесса во время отправки также может привести к повтору после истечения захвата.
Истечение срока не подтверждает, что Telegram не получил сообщение.

Ожидающие первой попытки геокодирования записи не занимают места в пакете
отправки. После появления координат или результата `success`, `detail`,
`not_found`, `error` они автоматически возвращаются в выборку, когда наступит
`next_attempt_at`. Отфильтрованные кандидаты также не расходуют лимит отправки.

### Ручная проверка Avito

Staff-пользователь открывает «Ещё → Проверка Avito». Очередь содержит активные
объявления без подтверждения более 3/7/14/30 дней (по умолчанию 7), включая
скрытые объявления и малые площади, но без действующих исключений из рынка. Ссылка открывает Avito в новой вкладке.
«Доступно» подтверждает карточку текущим временем; «Недоступно» снимает активность,
но сохраняет историю и оценки. Капча и сетевой сбой не означают снятие.
«Обновить цену» принимает целое число рублей, пересчитывает цену за м² по текущей
площади и подтверждает доступность. При неизвестной/нулевой площади цена за м²
очищается. История цены создаётся только при изменении суммы. Уведомления используют
действующие настройки Telegram и выполняются после commit.
Вкладка «Недоступные» позволяет исправить ошибку. Свежий XLSX с тем же Avito ID
также восстанавливает доступность и может обновить цену; отсутствие ID в файле
ничего не снимает. Повторный импорт старого файла тоже считается подтверждением,
поэтому загружайте свежую выгрузку. Ручная проверка доступности не подтверждает цену.
Перед запуском новой версии примените миграцию `0067_avitomanualcheck`.

## Ежедневная история рынка

После применения миграций выполните первый срез:

```bash
docker compose exec -T web python manage.py capture_market_snapshot
```

Для ежедневного накопления установите units из репозитория:

```bash
sudo cp deploy/systemd/home-hunter-market-snapshot.service deploy/systemd/home-hunter-market-snapshot.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now home-hunter-market-snapshot.timer
systemctl list-timers home-hunter-market-snapshot.timer
```

Таймер запускается в 23:55 Asia/Yekaterinburg. Это локальное сохранение БД,
а не опрос площадок, поэтому ограничения сетевых режимов `polling.py` к нему
не применяются. Наличие units в репозитории не означает включения на production.
При Persistent-запуске после простоя создаётся только срез текущего дня, без
выдуманного заполнения пропусков. Повтор за день не изменяет первый срез,
в том числе сделанный вручную. Поэтому время наблюдения видно на странице.

Страница «Ещё → Динамика рынка» доступна авторизованным пользователям.
Она показывает цены предложения; успешные опросы не подтверждают полноту рынка.
Срезы сохраняют включённые поиски и не зависят от личных скрытий. Для недельного
сравнения нужно восемь последовательных дневных наблюдений одинакового охвата
с успешным опросом каждого поиска за сутки. Пропуски показываются как отсутствие
данных, а не нулевые значения. Существующая история цен и snapshots не удаляется.

## Личные фотографии и полный backup

После обновления образов и миграции 0069 web должен быть пересоздан с новым
volume `viewing_media:/app/media`. Каталог внутри image принадлежит UID 10001;
при первом подключении Docker создаёт volume с его содержимым и правами.
Не добавляйте публичную раздачу `/media/` в Caddy. `MEDIA_ROOT` внутри web
должен указывать на каталог этого volume.

В «Ещё → Резервная копия БД» основная кнопка теперь скачивает одним архивом
БД и фотографии. Отдельный PostgreSQL dump не заменяет полный backup.
Храните копию на ноутбуке или другом носителе вне VPS. Состав архива, ограничения
фото и команды локального восстановления описаны в [VIEWINGS.md](VIEWINGS.md).

Операции с фото и полный backup согласуются через `.viewings.lock` в volume.
При занятом lock пользователь получает сообщение о повторе; резервное копирование
не запускает сетевые отправки и не изменяет данные объявлений.
