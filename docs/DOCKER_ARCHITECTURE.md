# Контейнеры Home Hunter

Документ описывает контейнеры проекта и процессы, которые запускаются рядом с
Docker на production-сервере.

## Постоянные сервисы

| Сервис | Контейнер | Назначение |
|---|---|---|
| `db` | `home_hunter-db-1` | PostgreSQL и постоянные данные приложения в volume `postgres_data`. |
| `web` | `home_hunter-web-1` | Django, Gunicorn, UI, API и management-команды. |
| `caddy` | `home_hunter-caddy-1` | Внешний reverse proxy. Принимает HTTP и передаёт запросы в `web`. |
| `telegram-poll` | `home_hunter-telegram-poll-1` | Приём входящих команд и оценок из Telegram. |

Обычная команда `docker compose up -d` поднимает `db`, `web`, `caddy` и
`telegram-poll`. PostgreSQL и Django-порт наружу не публикуются; внешний
HTTP-вход идёт через Caddy.

## Профильные сервисы

Эти сервисы включаются Docker Compose profiles и могут отсутствовать в списке
обычных сервисов:

| Профиль | Сервис | Контейнер | Назначение |
|---|---|---|---|
| `collector` | `polling-diagnostics` | `home_hunter-polling-diagnostics-1` | Постоянный браузерный процесс с Xvfb для диагностических запусков и тестов UI. |
| `collector` | `shared-socks-tunnel` | `home_hunter-shared-socks-tunnel-1` | SSH-туннель с SOCKS5 на `shared-socks-tunnel:1080`; через него идут маршрутизируемые запросы. |
| `collector` | `collector` | одноразовый контейнер | Avito-опрос. Завершается после выполнения команды. |
| `cian` | `cian-collector` | одноразовый контейнер | CIAN-опрос. Использует тот же `shared-socks-tunnel`. |
| `domclick` | `domclick-collector` | одноразовый контейнер | DomClick-опрос. Использует тот же `shared-socks-tunnel`. |

`shared-socks-tunnel` также входит в профили `cian` и `domclick`. Он слушает
порт 1080 только внутри Docker-сети; порт `127.0.0.1:19082` на хосте нужен для
локального агента DomClick и не является публичным прокси.

Примеры:

```bash
docker compose --profile collector up -d shared-socks-tunnel polling-diagnostics
docker compose --profile collector run --rm collector
docker compose --profile cian run --rm cian-collector
docker compose --profile domclick run --rm domclick-collector
```

## Процессы вне Docker-контейнеров

Плановые опросы, геокодирование и health-check запускаются systemd timers на
хосте. Они вызывают одноразовые Docker-команды и не обязаны постоянно иметь
свой контейнер в `docker ps`.

Telegram delivery worker также является systemd-сервисом:

```text
home-hunter-telegram-send.service
  docker compose exec -T web python manage.py telegram_send --interval 600 --batch-size 10
```

Он выполняется внутри уже работающего `web` и проверяет очередь публикаций раз
в 10 минут. Поэтому отдельного контейнера с именем вроде
`home_hunter-telegram-send-1` нет.

## Что не относится к Home Hunter

Контейнер `rss_bot` проекта `retail_digest` является отдельным приложением.
Он может находиться на том же сервере, но не использует контейнеры, базу и
Compose-сервисы Home Hunter.

## Диагностика

```bash
docker compose ps
docker compose config --services
docker compose config --profiles
docker compose logs --tail 100 web caddy telegram-poll
systemctl status home-hunter-telegram-send.service
systemctl list-timers 'home-hunter-*'
```

Если `polling-diagnostics` или `shared-socks-tunnel` есть в `docker ps`, но не
показываются в `docker compose config --services`, значит они запущены из
профиля. Это ожидаемое поведение Compose.
