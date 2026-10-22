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

## Модули

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

`listings/services/persistence.py` нормализует адрес, рассчитывает цену за м², применяет location rules и сохраняет историю. `listings/services/scans.py` создаёт Scan, обрабатывает `CollectionResult` и выполняет безопасную деактивацию только для завершённых проходов. Detail polling CIAN и Avito пока частично реализован непосредственно в management-командах.

### Модели

`Listing` и инфраструктурные модели пока остаются в `listings/models.py`, а review-домен и detail/polling state вынесены в `listings/models_reviews.py` и `listings/models_polling.py` с re-export из legacy-модуля. `Listing` остаётся центральной моделью; остальные группы будут выноситься постепенно без изменения схемы БД.

### Фоновые процессы и эксплуатация

Сканирование, detail polling, геокодирование, Telegram polling/delivery и health checks запускаются через management-команды и `deploy/systemd`. Advisory locks PostgreSQL и filesystem `flock` защищают конкурирующие запуски. Docker разделяет web, collector, Telegram polling и proxy tunnel.

## Известные архитектурные ограничения

- `listings/views.py` содержит общие HTTP-обработчики и совместимые импорты;
  feed/detail находятся в `listings/views_listings.py`, review queue и consideration
  workflow — в `listings/views_reviews.py`, а операции очереди и сохранения review —
  в `listings/services/reviews.py`. Операционные страницы находятся в
  `listings/views_operations.py`, географические обработчики — в
  `listings/views_geo.py`, а выгрузки — в `listings/views_exports.py`.
  Настройки scoring находятся в `listings/views_preferences.py`.
- Правила отображения расписания опросов вынесены в
  `listings/services/polling_schedule.py`; `views.py` сохраняет только
  совместимые публичные импорты.
- `listings/views.py` сохраняет thin wrappers для URL-совместимости; новые workflow-правила не должны возвращаться в этот модуль.
- Market comparison находится в `listings/services/market.py`; выбор аналогов и
  пороги сравнения едины для web-аннотации и Telegram/deal tuple. Форматы полей
  веба и кортеж уведомления остаются разными только на границе представления.
- scoring реализован в `listings/services/scoring.py` и используется web и Telegram; `views.py` сохраняет только thin wrapper для совместимости импортов.
- CIAN и Avito detail commands дублируют persistence/snapshot/status workflow.
- Общие операции detail payload и перехода в `unavailable` вынесены в `listings/services/detail_persistence.py`; source-specific enrichment остаётся в командах.
- Lifecycle прогресса detail-циклов CIAN и Avito вынесен в `listings/services/detail_progress.py`; команды сохраняют только source-specific orchestration.
- Фильтрация feed/shortlist и поиск похожих объявлений вынесены в `listings/services/listing_queries.py`; `views.py` оставляет только совместимые wrappers.
- Медианная аналитика микрорайонов вынесена в `listings/services/market.py`; view только передаёт queryset и параметры периода.
- Агрегаты dashboard (дневные ряды, районные медианы, изменения цен и распределения) готовит `listings/services/dashboard.py`.
- Классификация состояния сканеров и контекст CIAN detail-цикла находятся в `listings/services/scan_dashboard.py`.
- География, scoring и market calculations частично выполняются request-time в Python.
- Telegram HTTP integration распределена между несколькими services.

## Целевые границы постепенного рефакторинга

1. Одна явно версионируемая market policy для web, Telegram и export; общий
   результат сравнения уже используется web и Telegram, следующим шагом будет
   подключение export без дублирования форматирования.
2. Views только принимают запрос, вызывают selectors/services и формируют response.
3. Collectors возвращают DTO и не знают о ORM persistence.
4. Detail polling использует общий persistence service.
5. Тяжёлые market/scoring операции переводятся в materialized snapshots только после измерения нагрузки.

Архитектурные import-проверки находятся в `listings/tests/test_architecture.py` и не требуют базы данных.

Полная перепись и немедленное введение новой очереди задач не требуются.
