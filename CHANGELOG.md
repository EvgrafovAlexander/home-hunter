# Changelog

История заметных изменений проекта Home Hunter.

## 2026-10-22

### Планировщик

- Расписание Avito fast переведено с ошибочного фактического интервала около 30 минут
  на явные двухчасовые слоты с небольшим случайным смещением до 10 минут.
- Расчёт «Следующего запуска» на странице опросов синхронизирован с новым расписанием.

### Архитектура

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
