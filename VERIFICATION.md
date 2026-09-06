# Проверка Home Hunter MVP — 2026-09-06

Окружение: macOS arm64, Python 3.12.2, Docker Desktop 28.3.2, PostgreSQL 17.
Production-конфигурация проверяется локально в Linux-контейнерах.

## Реализовано

- config/: Django settings/env, URL routing, WSGI.
- listings/: шесть моделей, initial migration, Admin, atomic persistence,
  Scan lifecycle, management command и тесты.
- collectors/: DTO, BaseCollector, registry, Avito selectors/parser/Playwright,
  HTML fixture и тесты.
- Dockerfile: web и collector targets; requirements разделены.
- docker-compose.yml: db/web/caddy и collector profile.
- docker-compose.local.yml: только локальная публикация PostgreSQL.
- deploy/: Caddyfile, два systemd service и два timer с общим flock.
- .env.example, .dockerignore, pytest.ini, README.md.

## Выполненные проверки

- Полный suite с optional настоящим Chromium: **37 passed**.
- makemigrations --check --dry-run: No changes detected.
- migrate --check и manage.py check: успешно.
- Local workflow: docker compose up -d db, migrate, runserver.
- Admin: создание SearchQuery через форму, доступ после login, все шесть разделов.
- HTTP runserver: login и CSS 200, закрытый Admin редиректит на login.
- Compose config: production и local конфигурации валидны.
- Production up -d: постоянно работают только caddy, db, web.
- PostgreSQL и Gunicorn в production не публикуют host ports; Caddy публикует 80.
- Production migrations/checks: успешно.
- Caddy validate: valid; HTTP login и CSS через Caddy возвращают 200.
- Финальный web image не содержит Playwright, Chromium и .env.
- Блокировка параллельного command проверена через отдельное PostgreSQL-соединение.

## Live Avito

Использован URL из существующего avito_pagination.py, обычный Chromium --headed,
без изменения экспериментального скрипта и без anti-bot bypass.

1. Fast: success, 60 карточек, 60 новых.
2. Повторный fast: success, 60 карточек, 1 новая; дубликатов PriceHistory/Snapshot нет.
3. Full: обработаны 3 страницы (60, 50, 50 карточек), затем HTTP 403.
   Scan корректно failed, 102 новых сохранены, деактивации не было.

В dev-БД сохранены 163 уникальных объявления. Полный успешный live-обход
не подтверждён из-за ограничения Avito. Логика успешного полного обхода
проверяется fixtures и тестами.

## Ограничения

- Настоящий VPS/public SERVER_IP не предоставлен: production проверен локально
  через Docker/Caddy на порту 80, а не на удалённом сервере.
- macOS не имеет systemd: установка/срабатывание timers и journalctl на host
  не проверены. Units и инструкции готовы для Linux.
- Docker images собраны и запущены для arm64; отдельная amd64-сборка VPS
  в этой проверке не выполнялась.

## Финальная проверка collector image

- Оба финальных Docker targets успешно собраны.
- `docker compose run --rm -T collector python manage.py shell < collectors/tests/docker_smoke.py`:
  успешно. Настоящий Chromium, UTF-8 fixture через перехват URL, fast и full до
  пустой страницы, проверка цены/комнат/этажа, ровно одна PriceHistory/Snapshot,
  browser закрывается после каждого command.
- `docker compose run --rm collector python manage.py collect_listings --source avito --mode fast`
  в пустой проверочной production-БД: exit 0, No enabled searches.
- После одноразовых запусков список постоянных services: caddy, db, web.

## Оставленное локальное окружение

Текущий .env настроен на Compose project home_hunter_local, БД home_hunter_dev,
localhost:55433. В ней 163 Listing, 163 PriceHistory, 163 ListingSnapshot.
Секреты/локальные настройки не включены в git.
Временные runserver и production-проверочные сервисы остановлены;
локальный Compose db оставлен работающим.

Продолжение работы:
```bash
source venv/bin/activate
python manage.py createsuperuser
python manage.py runserver
python manage.py collect_listings --source avito --mode fast --headed
```
