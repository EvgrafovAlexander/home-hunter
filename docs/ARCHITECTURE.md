# Архитектура Home Hunter

## Фактическая схема

Home Hunter — Django-монолит с PostgreSQL. Web-приложение запускается Gunicorn, фоновые процессы — management-командами из systemd timers/services. Celery, Redis и отдельный REST API в проекте не используются.

```text
SearchQuery
  -> polling profile / route
  -> source collector (Avito, CIAN, DomClick)
  -> NormalizedListing
  -> listings.services.persistence.process_listing()
  -> Listing + ListingSearchQuery + PriceHistory + ListingSnapshot
  -> Django views/templates, XLSX export, Telegram delivery
```

Для Avito `SearchQuery` может содержать обычный HTML URL (`url`) и соответствующий
URL Mobile API (`mobile_api_url`). Транспорт выбирается polling-профилем, поэтому
один запуск использует только одну форму запроса; оба collector'а передают данные
в общий `NormalizedListing` и persistence-контракт.

Для профилей Avito с `cookie_strategy=spfa` cookies и user-agent получает
`collectors.avito.spfa.SpfaClient`: Mobile API использует обязательный
авторизованный proxy, а Chromium получает обычные cookies и добавляет их в новый
контекст. Сохранённые cookies перед следующим запуском ставятся в бесплатную
очередь разблокировки; ответ `202` означает только принятие задания.

## Модули

Назначение и границы сервисных модулей собраны в отдельном фактическом каталоге
[`docs/SERVICES.md`](SERVICES.md). При расхождении каталога с кодом приоритетом
является исправление документации в том же изменении, а не молчаливое расширение
ответственности сервиса.

### Web и frontend

Маршруты находятся в `listings/urls.py`, HTTP-обработчики — в тематических модулях
`listings/views*.py` и `listings/agent_views.py`; `listings/views.py` сохраняет
совместимые публичные импорты и оставшиеся обработчики. Dashboard вынесен в
`listings/views_dashboard.py`, а назначение polling-профилей — в
`listings/views_polling_profiles.py`. UI — Django templates в `listings/templates/listings`
и `templates`; мониторинг запусков находится в `listings/views_scan_statistics.py`.
Пользовательские feed, shortlist и detail-страницы находятся в
`listings/views_listings.py`.
Операционные страницы резервного копирования, контроля исчезнувших объявлений,
недоступных карточек CIAN и качества данных находятся в
`listings/views_operations.py`.
Справочники локаций, пользовательские геозоны и карты находятся в
`listings/views_geo.py`.
Выгрузки пользовательских оценок и рыночного сравнения находятся в
`listings/views_exports.py`.
Настройки личной оценки находятся в `listings/views_preferences.py`.
Расчёт ближайших запусков и ограничения режимов находятся в
`listings/services/polling_schedule.py`.
JavaScript встроен в шаблоны, карта использует Leaflet. Отдельной
frontend-сборки и формального API schema layer нет.

### Collectors

`collectors/avito`, `collectors/cian` и `collectors/domclick` содержат source-specific parsers и Playwright/HTTP collectors. Общий DTO-контракт определён в `collectors/base.py`; выбор реализации выполняет `collectors/registry.py`. Сетевые профили и proxy resolution находятся в `collectors/routing.py`.

### Persistence и scan orchestration

`listings/services/persistence.py` нормализует адрес, рассчитывает цену за м², применяет location rules и сохраняет историю. `listings/services/scans.py` создаёт Scan, обрабатывает `CollectionResult` и выполняет безопасную деактивацию только для завершённых проходов. Detail polling использует общий orchestration-слой и source-specific persistence-сервисы; management-команды оставляют за собой получение данных площадки.

### Модели

`Listing` и инфраструктурные модели пока остаются в `listings/models.py`, а review-домен и detail/polling state вынесены в `listings/models_reviews.py` и `listings/models_polling.py` с re-export из legacy-модуля. `Listing` остаётся центральной моделью.

### Фоновые процессы и эксплуатация

Сканирование, detail polling, геокодирование, Telegram polling/delivery и health checks запускаются через management-команды и `deploy/systemd`. Advisory locks PostgreSQL и filesystem `flock` защищают конкурирующие запуски. Docker разделяет web, collector, Telegram polling и proxy tunnel.

## Границы и ограничения

Review workflow находится в `views_reviews.py` и `services/reviews.py`. Доменное сравнение цен выполняет `services/market.py`, персональную оценку — `services/scoring.py`. Экспорт форматирует эти результаты на границе представления; агрегатные листы имеют отдельную статистическую семантику.

Detail-команды получают данные источника. Сохранение выполняют `cian_detail_persistence.py` и `avito_detail_persistence.py`, общий переход в опубликованное состояние — `mark_detail_published()`. Прогресс циклов обслуживает `detail_progress.py`, выбор кандидатов и общий цикл — `detail_polling.py`.

Telegram-публикации проходят очередь `telegram_delivery.py`. Постоянный `telegram_send` запускается через systemd внутри контейнера web. Перед отправкой проверяются актуальные данные объявления и зоны пользователя; состояния доставки не заменяют географическую классификацию.

Часть географических, рыночных и scoring-расчётов выполняется в Python во время запроса. Внешние Telegram-вызовы распределены по нескольким сервисам. Эти ограничения сами по себе не являются основанием вводить новую очередь или отдельный frontend.

Проверки границ импортов: `listings/tests/test_architecture.py`. Правила дальнейшей разработки: [AGENTS.md](../AGENTS.md).

Безопасность полного цикла CIAN накапливается в checkpoint: небезопасный пакет
запрещает учёт исчезнувших объявлений для всего прохода, даже если последний
пакет завершился без ошибок. Миграция 0066 помечает уже существующие checkpoint
небезопасными, поскольку качество ранее прочитанных пакетов неизвестно.
Новый цикл и смена URL поиска начинают проверку заново.
