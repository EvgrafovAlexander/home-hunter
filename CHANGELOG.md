# Changelog

История заметных изменений проекта Home Hunter.

## 2026-10-22

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
- `0d4ca50` — централизованы scoring и market comparison, добавлены правила
  разработки Codex и исходное архитектурное описание.
- Review queue, сохранение пользовательской оценки и список рассмотрения
  вынесены из общего `listings/views.py` в `listings/views_reviews.py` и
  `listings/services/reviews.py`.
- URL-имена и обратная совместимость импортов обработчиков сохранены.
