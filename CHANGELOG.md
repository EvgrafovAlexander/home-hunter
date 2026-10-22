# Changelog

История заметных изменений проекта Home Hunter.

## Исправления интерфейса

- Удалён лишний служебный текст со списком заголовков и рыночных подписей из
  ленты объявлений и страницы «Готов рассмотреть».
- Исправлена ошибка 500 на странице shortlist: вынесенный обработчик получил
  недостающие импорты времени для фильтра «за 7 дней».
- Добавлены регрессионные HTTP-тесты для shortlist, consideration и CSV-выгрузки
  после декомпозиции view-модулей.
- Исправлен недостающий импорт `AvitoDetailPayload` в вынесенном обработчике
  детальной страницы объявления.
- Настройки личной оценки вынесены в `listings/views_preferences.py`; сохранены
  URL и правила нормализации параметров.
- Расчёт ближайших запусков и блокировка небезопасных режимов вынесены в
  `listings/services/polling_schedule.py`; совместимые импорты сохранены.

## 2026-10-22

### Планировщик

- Расписание Avito fast переведено с ошибочного фактического интервала около 30 минут
  на явные двухчасовые слоты с небольшим случайным смещением до 10 минут.
- Расчёт «Следующего запуска» на странице опросов синхронизирован с новым расписанием.

### Архитектура

- В `listings/services/market.py` введён общий результат сравнения объявлений:
  web и Telegram теперь используют одинаковый выбор аналогов, пороги площади,
  fallback и оценку отклонения цены.
- Лист «Квартиры» в XLSX-выгрузке переведён на тот же per-listing расчёт рынка;
  в него добавлены ориентир, число аналогов, уверенность и отклонение цены.
- Общая orchestration-логика detail-опросов CIAN и Avito вынесена в
  `listings/services/detail_polling.py`; команды оставляют source-specific fetch
  и сохранение payload.
- Сохранение CIAN detail вынесено в `listings/services/cian_detail_persistence.py`:
  применение полей, location enrichment, snapshots и статусы больше не смешаны
  с management-командой.
- Сохранение Avito detail вынесено в `listings/services/avito_detail_persistence.py`:
  payload, фотографии, координаты, snapshots и статусы больше не смешаны с
  management-командой.
- Общая финализация опубликованных CIAN и Avito detail вынесена в
  `mark_detail_published()`; одинаковые обновления Listing и detail state больше
  не дублируются.

- Dashboard вынесен из общего `listings/views.py` в `listings/views_dashboard.py`;
  URL и публичный импорт обработчика сохранены.
- Назначение polling-профилей вынесено в `listings/views_polling_profiles.py`;
  URL и права staff-only сохранены.
- Мониторинг запусков и health-состояний вынесен в
  `listings/views_scan_statistics.py`; POST-действия и URL сохранены.
- Feed, shortlist, hidden feed и detail объявлений вынесены в
  `listings/views_listings.py`; URL и совместимые импорты сохранены.
- Операционные страницы резервного копирования, контроля исчезнувших объявлений,
  недоступных карточек CIAN и качества данных вынесены в
  `listings/views_operations.py`; URL и права доступа сохранены.
- Справочники локаций, пользовательские геозоны и карты вынесены в
  `listings/views_geo.py`; URL и права доступа сохранены.
- Выгрузки пользовательских оценок и рыночного сравнения вынесены в
  `listings/views_exports.py`; форматы файлов и URL сохранены.
- Общие операции detail payload и перехода объявления в `unavailable` вынесены
  в `listings/services/detail_persistence.py` для CIAN и Avito.
- Общий lifecycle прогресса detail-циклов вынесен в
  `listings/services/detail_progress.py`; добавлены проверки current/completed.
- Фильтры, варианты фильтров и поиск похожих объявлений вынесены в
  `listings/services/listing_queries.py`.
- Медианная аналитика микрорайонов перенесена из views в market service;
  правила русскоязычных лаконичных комментариев добавлены в `AGENTS.md`.
- Агрегаты dashboard вынесены в `listings/services/dashboard.py`; сервисные
  модули получили русскоязычные описания назначения.
- Классификация health и контекст CIAN detail-цикла вынесены в
  `listings/services/scan_dashboard.py`.
- Review-модели вынесены в `listings/models_reviews.py`; публичные импорты из
  `listings.models` и схема БД сохранены.
- Detail/polling-модели вынесены в `listings/models_polling.py`; публичные
  импорты и схема БД сохранены.
- `0d4ca50` — централизованы scoring и market comparison, добавлены правила
  разработки Codex и исходное архитектурное описание.
- Review queue, сохранение пользовательской оценки и список рассмотрения
  вынесены из общего `listings/views.py` в `listings/views_reviews.py` и
  `listings/services/reviews.py`.
- URL-имена и обратная совместимость импортов обработчиков сохранены.
