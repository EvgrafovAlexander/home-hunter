# Методы опроса и добавление новых способов

## Как выбирается способ

`PollingProfile` связывает источник и режим (`fast`, `full`, `detail`) с маршрутом,
транспортом и cookies. На странице `/polling-profiles/` можно создать профиль и
назначить его через `SourcePollingProfile` или сделать его default.

Старые профили без явных настроек сохраняют прежнее поведение: CIAN и Домклик
работают через Chromium, а основной Avito — через текущий HTML-коллектор.

Для Avito доступны два независимых слоя:

1. `browser_html` — обычный сбор поисковой выдачи;
2. `mobile_api` — экспериментальный API-сбор выдачи и отдельная команда detail.

Detail не включается автоматически. Его запускают отдельно:

```bash
python manage.py poll_avito_details --profile avito_mobile_detail --limit 5
```

Профиль detail должен использовать `mobile_api` и `own_cookies` либо `spfa`.
Cookies профиля хранятся в `AVITO_STATE_DIR/profiles/<code>/cookies.json`.

## Как добавить новый способ опроса

1. Добавить значение в `PollingProfile.Transport` или `CookieStrategy` и миграцию.
2. Реализовать коллектор с контрактом `BaseCollector` либо отдельный detail-клиент.
3. Подключить выбор метода в `collectors/registry.py`.
4. Добавить нормализацию в `NormalizedListing` без изменения общей persistence-логики.
5. Для второго этапа создать source-specific `PollState`, `Payload`, `Progress`,
   parser и management command по образцу `poll_avito_details.py`/`poll_cian_details.py`.
6. Добавить тесты parser, routing, profile isolation и сохранения detail.
7. Добавить systemd timer только после ручной проверки; ночной запрет должен быть
   отражён в самом timer (`OnCalendar`) и не использовать `Persistent=true`.

Не следует менять default-профиль существующего источника только ради тестирования
нового транспорта: новый способ вводится отдельным профилем и явным `--profile`.

## Ночное расписание CIAN

CIAN fast, detail и full теперь ограничены дневным окном 08:00–23:00. Для detail
используются четыре календарных правила по 15 минут, full перенесён с 00:10 на
08:10. У таймеров CIAN отключён `Persistent=true`, чтобы пропущенный ночной запуск
не выполнялся автоматически после старта сервера.

После обновления units на сервере:

```bash
systemctl daemon-reload
systemctl disable --now home-hunter-cian-fast.timer home-hunter-cian-detail.timer home-hunter-cian-full.timer
systemctl enable --now home-hunter-cian-fast.timer home-hunter-cian-detail.timer home-hunter-cian-full.timer
systemctl list-timers --all | grep home-hunter-cian
```
