# Changelog

История заметных изменений проекта Home Hunter.

## 2026-10-22

### Планировщик

- Расписание Avito fast переведено с ошибочного фактического интервала около 30 минут
  на явные двухчасовые слоты с небольшим случайным смещением до 10 минут.
- Расчёт «Следующего запуска» на странице опросов синхронизирован с новым расписанием.

### Архитектура

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
