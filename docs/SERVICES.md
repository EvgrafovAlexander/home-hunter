# Каталог сервисов Home Hunter

Документ описывает фактические границы файлов в `listings/services/`. Он нужен
перед изменением сервиса: сначала проверьте, подходит ли задача его текущей
ответственности, и только затем меняйте код.

`__init__.py` задаёт публичную границу сервисного пакета и не содержит доменной
логики.

## Сохранение и detail polling

| `spfa_audit.py` | Fail-closed учёт вызовов SPFA и сохранение снимков cookies Avito | `AvitoSpfaRequest`, `AvitoSpfaCookie` |

| Модуль | Назначение | Изменяет данные / side effects |
|---|---|---|
| `persistence.py` | Нормализация `NormalizedListing`, location rules, сохранение `Listing`, истории и snapshots | ORM-транзакции |
| `detail_persistence.py` | Общие операции payload, недоступности и финализации опубликованного detail | Payload-модели, `Listing`, `ListingSnapshot`, detail state |
| `detail_polling.py` | Выбор кандидатов и общий последовательный цикл detail-опроса | Обновляет progress/state через переданные callbacks |
| `detail_progress.py` | Состояние циклов detail и список завершённых объявлений | ORM-транзакции над progress-моделями |
| `cian_detail_persistence.py` | Применение CIAN detail, адрес, геокодирование и source-specific snapshot | `Listing`, CIAN payload/state, snapshots, `PriceHistory`, Telegram score после commit |
| `avito_detail_persistence.py` | Применение Avito detail, фотографий и координат | `Listing`, Avito payload/state, snapshots |
| `scans.py` | Жизненный цикл Scan и безопасная деактивация по результату `CollectionResult` | `Scan`, объявления и snapshots |

Collectors получают данные, а persistence-сервисы сохраняют их. Detail-команды
не должны напрямую дублировать эти операции.

## Рынок, scoring и фильтры

| Модуль | Назначение | Граница |
|---|---|---|
| `market.py` | Единая политика сравнения цены, медианы и аналогов | Не формирует HTTP или Telegram-текст |
| `market_export.py` | Формирование XLSX-файла рыночной аналитики | Форматирует файл, но не определяет отдельную per-listing market policy |
| `scoring.py` | Объяснимый персональный Fit score | Не выполняет доставку уведомлений |
| `personal_filters.py` | Геозоны и жёсткие пользовательские фильтры | Чистые проверки и аннотация объектов |
| `listing_queries.py` | Queryset-фильтры, варианты фильтров и похожие объявления | Не рендерит шаблоны |
| `reviews.py` | Review queue, consideration и пользовательские решения | ORM-транзакции review-домена |
| `change_history.py` | Человекочитаемое описание изменений snapshots | Преобразование данных без внешних side effects |

## Локации и производные данные

| Модуль | Назначение | Внешние зависимости |
|---|---|---|
| `enrichment.py` | Расчёт цены за м² и разбор адреса | Не меняет ORM самостоятельно |
| `location_directory.py` | Сопоставление адресов со справочниками районов и микрорайонов | ORM-справочники локаций |
| `microdistrict_polygons.py` | Поиск микрорайона по кэшированным полигонам | Файл GeoJSON и ORM-кэш полигонов |
| `geocoding.py` | Запрос координат у внешнего геокодера | Сетевой HTTP-запрос, настройки и таймауты |

## Dashboard, расписание и контроль качества

| Модуль | Назначение |
|---|---|
| `dashboard.py` | Подготовка дневных и районных агрегатов для dashboard |
| `scan_dashboard.py` | Классификация состояния сканеров и detail-циклов |
| `scan_health.py` | Проверка здоровья запусков и отправка health-уведомлений |
| `polling.py` | Разрешение режимов и временных ограничений опросов |
| `polling_schedule.py` | Расчёт ближайших запусков и расписания для интерфейса |
| `database_backup.py` | Создание временного PostgreSQL backup для авторизованной выгрузки |

## Telegram и уведомления

| Модуль | Назначение | Side effects |
|---|---|---|
| `deal_alerts.py` | Оценка и публикация новых выгодных объявлений | Telegram API, очереди и модели alert |
| `price_alerts.py` | Обновление сообщений при изменении цены | Telegram API и модели alert |
| `telegram_delivery.py` | Идемпотентная очередь доставки публикаций | Claims, статусы и Telegram API через delivery workflow |
| `scan_summary.py` | Личные сводки CIAN/Avito-опросов | Итоги Scan и время следующего запуска через Telegram |
| `telegram_reviews.py` | Интерактивная review-сессия в Telegram | Telegram API и review-модели |

## Правила изменения

- HTTP-обработчики не добавляются в сервисы.
- Collector и parser не вызывают ORM-сервисы сохранения напрямую в обход
  orchestration-команды.
- Внешний side effect должен быть явно виден в описании модуля или публичной
  функции и по возможности выполняться после commit.
- Если ответственность сервиса меняется, обновите эту таблицу,
  `docs/ARCHITECTURE.md` и `CHANGELOG.md` в одном коммите.
