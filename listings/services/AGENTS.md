# Правила для listings/services

- Сервисный слой содержит application/domain operations, а не HTTP rendering.
- Сервисы не импортируют `listings.views`; view может вызывать сервис, обратное направление запрещено.
- Scoring, market comparison, enrichment и location rules имеют одного владельца; не дублируйте алгоритмы в Telegram, export и views.
- Web-compatible wrappers могут временно оставаться в `views.py`, но реализация должна находиться в сервисе.
- ORM-транзакции обозначаются явно. Внешние side effects выполняются после commit и должны быть идемпотентными.
- Интеграционный HTTP-код отделяется от расчётов и persistence.
- Публичные функции сервисов покрываются тестами; private-функции другого сервиса не являются API.
