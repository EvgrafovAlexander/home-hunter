# Home Hunter: правила разработки

## Архитектура

- Проект остаётся Django-монолитом с PostgreSQL, server-rendered templates и management-командами.
- Не добавляйте Celery, Redis, REST-фреймворк или отдельный frontend без измеренной проблемы и отдельного архитектурного решения.
- Направление зависимостей: `views/templates -> application services/selectors -> models`; `commands -> services`; `collectors -> normalized DTOs`.
- `collectors` не изменяют ORM и не импортируют `listings.views`.
- Доменные расчёты (scoring, market comparison, location rules) не размещаются во views.
- Review queue и consideration workflow поддерживаются в `listings/views_reviews.py` и `listings/services/reviews.py`; `listings/views.py` содержит только URL-совместимые wrappers.

## Изменения данных и side effects

- Ingestion должен быть идемпотентным при повторной обработке одного объявления.
- Границы транзакций должны быть явными; внешние отправки запускаются после commit через `transaction.on_commit`.
- Не удаляйте исторические snapshots, price history, scan records и polling state.
- Миграции должны быть backward-compatible; destructive migration требует отдельного согласования.
- Секреты, cookies, proxy credentials и production dumps не добавляются в git и не пишутся в логи.

## Collectors

- Parser — чистое преобразование HTML/JSON в DTO.
- Collector отвечает за transport, browser/session lifecycle, pagination и block/cooldown policy.
- Общий контракт — `collectors.base.NormalizedListing` и `CollectionResult`.
- Partial/failed scan не должен деактивировать объявления.

## Тестирование

Перед завершением изменения запускайте применимые проверки:

```bash
venv/bin/python manage.py check
venv/bin/python manage.py makemigrations --check --dry-run
venv/bin/pytest
```

Live browser tests запускаются отдельно и не должны быть обязательной частью обычного unit-test прогона.

## Документация

- Фактическая архитектура описывается в `docs/ARCHITECTURE.md`.
- При изменении границ модулей, фоновых процессов, моделей или внешних интеграций обновляйте архитектурную документацию в том же изменении.
- Каждое заметное изменение фиксируйте в `CHANGELOG.md` в том же коммите, что и код.
- Не описывайте будущую архитектуру как уже существующую.
