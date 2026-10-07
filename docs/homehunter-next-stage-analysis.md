# HomeHunter: технический и продуктовый анализ следующего этапа

Дата анализа: 2026-10-07. Документ основан на фактическом состоянии репозитория; новые функции и изменения рабочего кода в рамках анализа не выполнялись.

## 1. Executive summary

HomeHunter — Django-приложение с PostgreSQL, серверными HTML-шаблонами и отдельными браузерными коллекторами CIAN, Avito и Домклик. Уже реализованы: нормализация карточек, сохранение истории цен, snapshots изменений, геокодирование, справочник районов/микрорайонов, рыночные медианы, пользовательские оценки, shortlist, карта, CIAN detail polling, Telegram-уведомления и XLSX-экспорт.

Главный архитектурный долг — модель `Listing` описывает объявление конкретного источника, но одновременно используется как физическая квартира: в ней хранятся источник, external id, URL, характеристики, рынок, пользовательский review и workflow. Поэтому дедупликация, общая история физического объекта, cross-source цены и устойчивый пользовательский workflow будут усложняться с каждым новым релизом.

Второй долг — текущий score смешивает в одной формуле соответствие пользователю, рыночную выгодность, свежесть и полноту данных. Жёсткие ограничения реализованы как обычные проверки, а не как фильтр: неподходящая локация может остаться высоко в выдаче благодаря другим факторам. Рынок и scoring считаются в request-time на Python-списках и повторяют похожую, но не одну и ту же логику в `views.py` и `services/deal_alerts.py`.

Наибольший эффект дадут следующие изменения:

1. ввести слой `Property` над текущими source listings, не удаляя старые `Listing`;
2. отделить hard filters и `Fit Score` от `Deal Score` и `Confidence Score`;
3. вынести comparable engine и результат его расчёта в переиспользуемый сервис/материализованный snapshot;
4. добавить пользовательские polygon/multipolygon-зоны и запретить зоне D компенсироваться ценой;
5. построить идемпотентные `ListingEvent` и экран «Что изменилось» на базе snapshots, PriceHistory и score snapshots;
6. только после этого добавлять workflow просмотра, финансовые сценарии, торг, AI-фото и транспорт.

Технический вывод: PostgreSQL уже используется, координаты и GeoJSON-полигоны уже есть, поэтому PostGIS полезен для зон и расстояний, но миграция должна быть постепенной. Celery/очередь задач в проекте не обнаружены: фоновые операции сегодня запускаются systemd timers/management commands (`collect_listings`, `geocode_listings`, `poll_cian_details`, Telegram polling). Это надо учитывать при проектировании фоновых расчётов.

## 2. Current architecture

### 2.1. Фактический поток данных

`SearchQuery → Collector → NormalizedListing → process_listing() → Listing/ListingSearchQuery/PriceHistory/ListingSnapshot → request-time market/scoring → Django view → template/map/XLSX/Telegram`

- Источник и расписание: `listings/models.py:SearchQuery`, `listings/management/commands/collect_listings.py`, `deploy/systemd/*`.
- Коллекторы: `collectors/avito/collector.py`, `collectors/cian/collector.py`, `collectors/domclick/collector.py`; выбор через `collectors/registry.py`.
- Общий контракт нормализации: `collectors/base.py:NormalizedListing`. В SERP-контракте есть цена, площадь, комнаты, этаж, адрес, район, описание, дата и главное фото. Расширенные характеристики приходят в основном из CIAN detail (`collectors/cian/parser.py:detail_attributes`).
- Персистенс: `listings/services/persistence.py:process_listing`. Здесь вычисляется `price_per_sqm`, нормализуется адрес, назначаются `District`/`Microdistrict`, сохраняются price history и snapshots, обновляется связь с поиском.
- Full scan: `listings/services/scans.py`. Для CIAN используется checkpoint и деактивация только после двух пропущенных полных проходов; для других источников full scan деактивирует unseen listings после завершённого цикла.
- CIAN detail: `listings/management/commands/poll_cian_details.py`; модели `CianDetailPollState`, `CianDetailPayload`, `CianDetailPollProgress`.
- Frontend: URL-маршруты в `listings/urls.py`, серверные templates `listings/templates/listings/*.html`, CSS в `listings/static/listings/app.css`. Карта — Leaflet в `map.html` и `microdistrict_map.html`.
- Экспорт: `listings/views.py:market_export_xlsx` → `listings/services/market_export.py:build_market_xlsx`.

### 2.2. Модели и их ответственность

- `SearchQuery`: сохранённый запрос источника, не объект недвижимости.
- `Listing`: уникален только по `(source, external_id)`; содержит source identity, текущие характеристики, координаты, visibility/activity, даты наблюдения и публикации.
- `ListingSearchQuery`: связь объявления с запросом; `missed_full_scans` и `is_active` участвуют в обнаружении снятия.
- `PriceHistory`: цена по времени, но без источника наблюдения/причины.
- `ListingSnapshot`: JSON текущих значимых полей, используется для change log и рыночной статистики снятых объявлений.
- `District`, `Microdistrict`, `MicrodistrictBoundary`, `StreetAssignment`: административный справочник и cached geometry. Модель полигона хранит GeoJSON в `JSONField`, не PostGIS geometry.
- `ScoringPreference`: единственные пользовательские настройки scoring; поля содержат минимальные площади, этаж, район, ремонт и веса market/freshness/data/floor/history/preference/location/condition.
- `ListingReview`, `ListingReviewRevision`, `ReviewTag`: оценка 1–10, решение `consider/reject`, текст, теги и ревизии.
- `Consideration`: текущий shortlist workflow (`new`, `planned`, `viewed`, `archived`) через OneToOne к review.
- Системные/глобальные скрытия: `UserListingHide`, `GlobalListingHide`.
- Источник-специфические статусы и алерты: `CianDetailPollState`, `DealAlert`, `TelegramListingAlert`, `TelegramPriceAlert`.

### 2.3. Явные API/endpoints и UI

Основные endpoints задаются в `listings/urls.py`: `/`, `/shortlist/`, `/reviews/`, `/considerations/`, `/my-reviews/`, `/my-reviews/export.xlsx`, `/listings/<id>/`, `/dashboard/`, `/scans/`, `/disappeared-listings/`, `/unavailable-cian/`, `/data-quality/`, `/directory/`, `/scoring-settings/`, `/map/`, `/microdistricts/map/`. Agent API — `/api/domclick-agent/jobs/*`; Telegram webhook — `/api/telegram/webhook/`.

Это не отдельный JSON REST API: большая часть данных формируется Django views и передаётся server-rendered templates. Для следующего этапа нужен небольшой read API для feed/change cards/score explanations, но мигрировать весь UI на SPA не требуется.

В репозитории не обнаружен слой DRF serializers/schemas или отдельный frontend build: контракты сейчас неформально задаются context dictionaries из views и полями моделей. Поэтому новые API DTO следует вводить явно (например, `listings/api/serializers.py` или dataclass-схемы), не отдавая наружу внутренние JSON snapshots.

### 2.4. Источник → нормализация → БД

CIAN SERP читает embedded JSON и карточки (`parse_page`), detail читает structured offer data, фото и координаты. Avito и Домклик разбирают HTML-карточки; у Avito площадь/этаж/комнаты извлекаются из title, у Домклик — тоже из title. Все три источника возвращают `NormalizedListing`.

`process_listing()` делает canonicalization, но не хранит raw source snapshot кроме CIAN detail payload. У Avito/Домклик сохраняется только нормализованный текст и главное фото URL. Полного массива изображений и локального объекта фото сейчас нет.

### 2.5. Что именно попадает в XLSX

`market_export.py` формирует листы «Обзор», «Методика», «Квартиры», «Оценки», «Цены», «CIAN статусы», «Рынок микрорайонов», «Рынок по комнатам».

- «Слой данных»: `_data_layer()` — `Кандидат — от 55 м²`, `Резервный аналог — 45–54 м²`, `Вне рабочей выборки`; пороги 55/45 м².
- Район/микрорайон: `Listing.district`, `Listing.microdistrict`.
- Системный балл: **в текущий XLSX не выгружается**; он временно вычисляется в `views.add_listing_score()` и не является полем БД. Это несоответствие ожиданиям экспорта.
- Пользовательская оценка и решение: `ListingReview.rating`, `ListingReview.decision` текущего пользователя.
- Shortlist: `Consideration.stage` через review.
- Цена за м²: `Listing.price_per_sqm`, вычисленная в persistence или пришедшая с источника.
- История цены: лист «Цены» из `PriceHistory`; изменение вычисляется последовательно по listing.
- Активность/видимость: `Listing.is_active`, `Listing.is_visible`, `publication_status`.
- CIAN status: `CianDetailPollState.status`, `last_checked_at`, `last_error`.
- Фото: только `Listing.image_url`; массив CIAN-фото живёт в `CianDetailPollState.detail_data`/`CianDetailPayload` и в listing sheet не раскрывается.

## 3. Current scoring

### 3.1. Фактическая формула

`views.add_listing_score(listings, preferences)` в `listings/views.py:324` рассчитывает для каждого listing временные компоненты 0–100 и взвешенное среднее:

```
score = sum(component_value * ScoringPreference.<weight>) / sum(active weights)
```

Компоненты:

- `market_weight`: от `add_market_position()`. Цена за м² сравнивается с медианой local group; при delta ≤ −10: 75, −4…−1: 68, +4…+9: 22, ≥+10: 0, иначе 50. Затем отклонение стягивается к 50 коэффициентом `min(1, samples/20)`.
- `freshness_weight`: 100 до 1 дня, 80 до 3, 65 до 7, 42 до 30, 20 старше.
- `data_weight`: адрес 35, район 15, микрорайон 15, цена 15, площадь 10, главное фото 10. Максимум 100.
- `floor_weight`: 20 для 1-го/последнего этажа, 100 для остальных, 55 при отсутствии полной информации.
- `price_history_weight`: 100 при снижении, 25 при повышении, 55 без изменения/истории.
- `condition_weight`: `repair_type`: euro 85, cosmetic 60, withoutRepair 20; текстовые эвристики ремонта 20/85; неизвестно 50.
- `preference_weight`: процент выполненных пользовательских checks. Проверки — min area, min kitchen area, floor range, preferred districts, photo, repair type, lift, balcony, furniture.
- `location_weight`: при `use_ufa_target_zones`: `target_location_score()` относительно пяти hard-coded центров. ≤0.7 км — 100; ≤1.5 — 78; ≤2.5 — 52; дальше 18; без координат 50.

Весовые значения по умолчанию заданы в `ScoringPreference`: market 45, freshness 20, data 15, floor 10, price history 10, preference 20, location 35, condition 25; в формулу попадают только компоненты, для которых вес атрибута не равен нулю.

Отдельная Telegram-формула есть в `listings/services/deal_alerts.py:_listing_score()`: шкала 0–10, старт 5, discount ±, площадь, административные районы, этаж, ремонт, фото, адрес. Она не совпадает с web score и должна быть заменена общим сервисом.

### 3.2. Проблемы текущего score

1. Hard constraints не hard: `preferred_districts`, minimum area, floor, lift и balcony только уменьшают `preference_weight`, а не исключают listing. Даже location 18 или 52 может быть компенсирована market/freshness/condition.
2. Цена/рынок смешаны с fit и качеством данных. Пользователь не видит, что высокий score получен за счёт скидки, а не соответствия.
3. Районный fallback может сравнить разные дома и классы жилья; `MARKET_MIN_ROOM_GROUP=8` и `MARKET_MIN_DISTRICT_GROUP=12` допускают широкие группы.
4. Score не сохраняется как версия расчёта. Исторически объяснить, почему карточка вчера была 82, нельзя.
5. `image_url` — один URL: наличие фото получает баллы, но качество/полнота набора неизвестны.
6. Отсутствие данных часто нейтрально (50/55), что может завышать incomplete listings.

Конкретный ответ на риск из постановки: да, квартира вне допустимой пользователем географии сейчас может получить высокий итоговый score, если у неё высокая свежесть, заполненность, хороший ремонт и/или рыночная скидка. `target_location_score=18` не является фильтром.

## 4. Data model problems

- Нет `Property`: невозможно надёжно различить физическую квартиру и публикации CIAN/Avito/Домклик.
- Локация дублируется в свободном тексте (`district`, `microdistrict`) и FK (`district_ref`, `microdistrict_ref`); есть manual overrides, polygon confidence и source, но нет единого `LocationEvidence`.
- Price history принадлежит listing, поэтому кросс-площадочные цены не объединяются.
- Review и `Consideration` связаны с listing; перенос пользовательского решения на duplicate listing потребует ручного копирования.
- Нет событий доменного уровня: `ListingSnapshot` — JSON diff-источник, а `Scan`, `TelegramPriceAlert` и `DealAlert` не образуют единую ленту.
- Нет versioned score/comparable result и объяснений Confidence.
- Фото представлены URL/JSON; нет `ListingPhoto`, perceptual hash, download state, source order и legal retention policy.
- Нет модели финансового профиля, viewing dossier, transport destination, data quality issue и duplicate match.
- `Listing.publication_status` бинарен; переходы «вернулся», «продано», «снято», «не подтверждено» не моделируются как история состояния.

## 5. Proposed architecture

Целевая схема:

```
Source card → Parser → Normalized Listing → Listing (source identity)
                                      ↘ Property matching ↔ DuplicateMatch
Property + UserPreference + GeoZone → Eligibility → FitScore
Property/Listing + ComparableSet + PriceHistory → DealScore
All evidence → ConfidenceScore + DataQualityIssue
Changes in ingestion/score/matching → ListingEvent → What changed feed
Property → CandidateState/Viewing/Finance/Negotiation dossier
```

Принципы:

- source-specific observations остаются неизменными и аудируемыми;
- пользовательские настройки не изменяют shared market facts;
- hard filter выполняется до Fit Score;
- `Fit`, `Deal`, `Confidence` показываются отдельно и имеют version/evidence;
- дорогостоящие расчёты materialize-ся background job-ом, API читает последний валидный snapshot;
- сомнительное matching не объединяет данные автоматически.

PostGIS рекомендуется на P0/P1 для `GeoZone.geometry`, spatial index и `ST_Contains/ST_DWithin`. До миграции можно использовать текущий GeoJSON и `shapely` в job, но не оставлять географию в request-time Python при росте объёма.

## 6. Proposed data model

Ниже только сущности, которых не покрывают существующие модели.

### 6.1. `Property`

Физическая квартира/объект: `id`, stable `public_key`, canonical address, lat/lon/point, district/microdistrict FK, rooms, area, floor, floors_total, built_year, normalized feature JSON, created/updated. `Listing.property_id` nullable на миграционный период. Нельзя автоматически считать source listing property без match evidence.

### 6.2. `DuplicateMatch`

`left_listing`, `right_listing` (или listing-property candidate), `status`: `confirmed_duplicate`, `probable_duplicate`, `possible_duplicate`, `not_duplicate`; `score` 0–1; `features` JSON (address/geo/area/rooms/floor/photos/description/agent); `model_version`, `reviewed_by`, timestamps. Unique unordered pair. Только confirmed duplicate автоматически attach-ит оба listing к одному Property.

### 6.3. `UserPreference` / `GeoZone`

Можно расширить `ScoringPreference`, но для чистого контракта рекомендован `UserPreference` OneToOne: hard constraints (rooms, min/max area, budget, floors, required features), soft weights, repair/house preferences. `GeoZone`: user, name, tier A/B/C/D, geometry Polygon/MultiPolygon, active, version, source, created/updated. D — hard exclusion; отсутствие координат даёт `unknown`, а не автоматическое A/B.

### 6.4. `CandidateState`

OneToOne `(user, property)` или `(user, listing)` на переходном этапе: `status` (`new`, `interesting`, `watching`, `call`, `viewing_scheduled`, `viewed`, `negotiation`, `deal`, `rejected`, `sold`, `removed`), `rating` не хранится здесь (остаётся в `ListingReview`), timestamps, `reason`, `changed_by`. Existing `Consideration` мигрируется в него через mapping.

### 6.5. `Viewing` и dossier

`Viewing`: candidate_state, scheduled_at, completed_at, result, created_by, notes. Структурированные поля лучше вынести в `ViewingObservation` или JSON schema с фиксированными keys: noise, entrance, elevator, windows, view, smell, parking, yard, plumbing, electrical, walls, floor, kitchen, bathroom, neighbors; каждое значение enum `good/acceptable/problem/unknown` плюс optional severity/comment. Фотографии пользователя — отдельный `ViewingPhoto` с file/object storage. Свободный `notes` остаётся для контекста.

### 6.6. `ListingEvent`

`user` nullable (global event), `property/listing`, `event_type`, `occurred_at`, `dedupe_key`, `payload`, `source_version`, `read_at`. Типы: new_candidate, price_drop, price_increase, unavailable, reactivated, characteristic_changed, duplicate_found, fit_changed, deal_changed, threshold_crossed. Unique `(user, dedupe_key)` гарантирует отсутствие повторов.

### 6.7. `ScoreSnapshot` и `ComparableSet`

`ScoreSnapshot`: listing/property, user, fit/deal/confidence, hard_filter state, component JSON, model_version, evidence ids, calculated_at. `ComparableSet`: target property, strict/expanded level, comparable ids, distance/features, median/quantiles, sample counts, generated_at, algorithm_version.

### 6.8. `FinancialProfile`, `DataQualityIssue`, `TransportDestination`, фото

- `FinancialProfile`: user, own_funds, untouchable_reserve, target_budget, absolute_max, custom currency/scenario settings.
- `DataQualityIssue`: listing/property, code, severity (`info/warning/error`), field, observed value, rule_version, status, first/last seen, resolved_by.
- `TransportDestination`: user, name/type, lat/lon, active; `TravelTimeSnapshot` к property/destination/mode/cache key.
- `ListingPhoto`: listing, source photo id/url, local object key, ordinal, sha256/perceptual hash, download status, fetched_at, removed_at. CIAN `detail_data.photos` backfill-ится постепенно.

## 7. Fit Score

### 7.1. Правила

Сначала вычисляется eligibility:

```
if zone == D or hard_budget_violation or hard_rooms_violation
   or hard_area_violation or required_feature_absent:
    eligible = false; Fit = 0; exclusion_reason = ...
```

Unknown не должен silently pass hard rule: политика пользователя задаёт `unknown_is_fail` для критичных полей и `unknown_is_penalty` для второстепенных.

После hard filter:

```
Fit = 0.30*location + 0.25*area_rooms + 0.15*condition
    + 0.10*layout_kitchen + 0.10*house + 0.05*floor + 0.05*extras
```

Рекомендуемая адаптация: location 30% разбить на zone tier (A=100, B=80, C=55, unknown=40), расстояние до зоны и транспортную доступность после появления сервиса. Area/rooms 25% должна использовать плавную близость к целевому диапазону, но hard min/max не компенсируется. Condition 15% — source field + AI/manual evidence, unknown 45–50 с confidence penalty. Layout/kitchen — kitchen ratio, bathrooms, windows/view, plan text; house — material, age, lift, parking, ceiling; floor — user preference + first/last penalty; extras — furniture, balcony, storage.

Bonuses допустимы только внутри eligible: наличие точного адреса, подтверждённого ремонта, хорошей планировки. Penalties: неизвестная характеристика, крайний этаж при нежелании, конфликтующие source values. Нельзя прибавлять bonus так, чтобы soft penalty перекрывал hard exclusion.

UI должен показывать Fit и раскрывать: eligible/excluded, tier зоны, компоненты, missing/unknown fields и правила.

## 8. Deal Score

Deal Score не зависит от пользовательских предпочтений, кроме опционального hard budget filter. Базовая модель 0–100:

```
Deal = 0.45*relative_price + 0.15*absolute_price_position
      + 0.10*price_history + 0.10*exposure
      + 0.08*competition + 0.07*negotiation_potential
      + 0.05*cross_source_advantage
```

- `relative_price`: robust percentile (1 − percentile текущей цены/м² среди strict comparables), capped by sample confidence.
- `absolute_price_position`: percentile абсолютной цены среди same-room/area class.
- `price_history`: снижение, число снижений, время с последнего снижения, без награды за шумные скачки.
- `exposure`: days since first_seen и inactive/reactivation history; долгий срок — сигнал, но не автоматическая скидка.
- `competition`: active comparable count и sell-side supply; отсутствие данных = neutral with lower confidence.
- `negotiation_potential`: explainable heuristic from duration, price cuts, defects/manual viewing notes, not a guaranteed discount.
- `cross_source_advantage`: cheaper confirmed duplicate/listing elsewhere.

При `<5` strict comparables Deal Score показывается с low confidence и не должен выдавать точность до единицы. UI: `Fit: 91`, `Deal: 64`, `Confidence: 86%`, рядом — reference set and caveats.

## 9. Confidence Score

Confidence — не субъективный балл привлекательности. Рекомендуемая объяснимая сумма независимых evidence:

```
Confidence = 100 * (0.25*C_comparables + 0.15*C_reserve
  + 0.15*C_completeness + 0.15*C_location + 0.15*C_price_history
  + 0.10*C_cross_source + 0.05*C_freshness)
  - anomaly_penalty
```

Каждый `C` нормирован 0–1:

- strict comparables: `min(1, strict_count/8)`;
- reserve comparables: `min(1, reserve_count/15)`;
- completeness: non-null weighted fields (address/point, rooms, area, floor, house, repair, photos);
- location: exact address/detail coordinates > geocoder street > district-only > unknown;
- price history: `min(1, observations/17)` с diminishing returns;
- cross-source: confirmed duplicate and matching features;
- freshness: age of evidence, capped after 90 days.

Anomaly penalty: −5 warning, −15 error per capped issue, conflict between duplicate sources −10. Display both number and evidence: «8 строгих аналогов, 17 наблюдений цены, точный адрес, 94% полей, duplicate match».

## 10. Comparable engine

### 10.1. Доступные признаки

Сейчас есть district/microdistrict, rooms, area, floor/floors_total, built_year (CIAN detail), repair, building material, lifts, kitchen/living area, bathrooms, balcony/loggia, parking, coordinates, price history. Для Avito/Домклик многие поля отсутствуют до отдельного detail enrichment. Нет стабильно извлечённой планировки, состояния подъезда, реального срока экспозиции и сделки.

### 10.2. Алгоритм

Уровни должны быть явными и сохраняться в `ComparableSet`:

1. strict: same canonical microdistrict, same rooms, area ±10% (и абсолютный разумный cap), valid price/m², candidate area class; optionally same building class/age band and floor band;
2. расширение A: same microdistrict + same rooms, area ±15%;
3. расширение B: same district + same rooms + area ±15%, same building class if available;
4. расширение C: same district + adjacent rooms/area class, explicit reserve;
5. fallback only if no usable local group: city segment, separately labelled.

Порядок приоритета: microdistrict → rooms → area → building type/year → floor → condition → extras. Similarity внутри уровня:

```
sim = .30*microdistrict + .20*rooms + .18*area_distance
      + .12*house + .08*floor + .07*condition + .05*extras
```

Hard filter исключает разные комнатности в strict, отсутствующую цену и явные data-quality errors. Расстояние допустимо для area/age/floor; missing feature не равен совпадению.

При `<5` строгих аналогов расширять только по одному правилу за шаг; UI обязан показать `strict=3, expanded_level=B, reserve=12`. Не смешивать 45–54 м² и 55+ незаметно — экспорт уже применяет такую маркировку и её следует перенести в engine.

Расчёт — background после изменения listing/property/comparables, результат материализуется. Request-time допускается только чтение готового набора.

## 11. Duplicate detection

Кандидаты matching генерируются по blocking keys: normalized street/house, geo cell, rooms, area bucket. Pair score:

```
0.30 address similarity + 0.20 geo proximity + 0.15 area
 +0.10 rooms + 0.08 floor/floors + 0.10 photo hashes
 +0.05 description/agent evidence + source-specific signals
```

Вес источника/агента не может заменить сильное противоречие площади/этажа. Thresholds: `≥0.90 confirmed`, `0.75–0.89 probable`, `0.60–0.74 possible`, ниже `not_duplicate`; для probable/possible — ручная очередь. Фото сравнивать perceptual hash, описание — normalized tokens; телефон/агент использовать только если поле реально получено и законно.

Переход `Listing → Property → Listings[]` затрагивает все обращения к `Listing`: views, `add_market_position`, `similar_listings`, `market_export`, reviews/Consideration, deal alerts, templates и tests. Делать в два этапа: nullable `property_id` + dual-read, затем backfill confirmed matches, затем property-level scoring/workflow. Не переписывать historical `Listing` ids и не удалять source rows.

## 12. Event system («Что изменилось»)

### 12.1. Источники событий

Уже доступны без изменения парсеров: создание/обновление в `process_listing`, `PriceHistory`, `ListingSnapshot`, full-scan deactivation/reactivation, CIAN detail status, current score inputs, `ListingSearchQuery.missed_full_scans`. `change_events()` уже умеет human-readable diff snapshots. Не хватает only: persistent event row, read cursor и score/duplicate event generation.

### 12.2. Идемпотентная генерация

После commit ingestion job создаёт события для diff. `dedupe_key` строится из `(listing/property, event_type, source_observation_id, old_value_hash, new_value_hash)`; unique constraint не допускает повтор. Для пользовательской ленты сохранять `last_seen_at`/`last_event_id` в User profile или `UserEventCursor`. Событие создаётся только при переходе состояния, а не на каждом polling с тем же значением.

Типы: `new_candidate`, `price_drop`, `price_increase`, `removed`, `reactivated`, `attribute_changed`, `duplicate_found`, `fit_changed`, `deal_changed`, `threshold_crossed`. Лента агрегирует counters и даёт ссылки на source evidence.

## 13. UI changes

Существующие страницы:

- `feed.html`: заменить один score на три badges, фильтр «eligible only», зона, Fit/Deal sort, confidence and event markers.
- `detail.html`: вкладки/секции «Объявление», «Рынок», «История», «Просмотр», «Юридическая проверка», «Торг»; cross-source listings и comparable evidence.
- `dashboard.html`: добавить «С последнего посещения», counters событий, distribution Fit/Deal/Confidence; оставить market KPIs.
- `consideration_list.html` и `review_queue.html`: CandidateState kanban/workflow; rating 1–10 отдельно.
- `scoring_settings.html`: hard filters, soft preferences, weights and zones; старые веса мигрировать, deprecated fields read-only.
- `map.html`/`microdistrict_map.html`: пользовательский polygon editor (не только staff), tier legend A–D, zone assignment.
- `data_quality.html`: перейти от трёх missing counters к DataQualityIssue queue.
- XLSX: добавить Property ID, Listing group, Fit, Deal, Confidence, comparable level, duplicate status и event summary; сохранить текущие листы.

Новые страницы: `/changes/` (лента), `/properties/<id>/` (агрегированное досье), `/finance/`, `/viewings/`, `/duplicates/` (ручная модерация), `/zones/`.

## 14. Migration strategy

1. Backward-compatible migrations: добавить nullable `Listing.property_id`, score/comparable tables и новые enum поля; старые URL/id не менять.
2. Backfill `Property` только для exact/confirmed matches: deterministic address+coordinates and manual review; unmatched listings получают singleton Property.
3. Перенести `ListingReview`/`Consideration` через `(user, property)` mapping, но сохранить FK listing и revisions. На период dual-write review updates записывать в обе модели.
4. Сохранить все `PriceHistory`, `ListingSnapshot`, `Scan`, `CianDetail*`; новые events строить из них с `backfill_source_id`, dedupe.
5. Мигрировать ScoringPreference в UserPreference без удаления колонок; legacy `add_listing_score()` заменить adapter-ом на новый service.
6. После 2–3 успешных циклов dual-read включить property-level UI; только затем сделать `property_id NOT NULL` для подтверждённых новых ingestion rows.
7. Никаких destructive deletes. Rejected/not duplicate сохранять как explicit statuses.

## 15. Performance considerations

| Операция | Где считать | Что хранить/кэшировать |
|---|---|---|
| normalization, price/m², basic quality | ingestion transaction | Listing fields + DataQualityIssue |
| geocoding, polygon classification | existing management command/background | coordinates, source/confidence, spatial index |
| duplicate blocking/matching | background batch after import | DuplicateMatch and review queue |
| strict comparables | background on listing/property/market change | ComparableSet + aggregates |
| Fit/Deal/Confidence | background per changed listing/user preference | ScoreSnapshot; API reads latest |
| market medians/percentiles | scheduled incremental job | materialized aggregate by geo/room/area period |
| events | same transaction for source diff, async for scores | ListingEvent unique dedupe key |
| photo download/hash/AI | background rate-limited | ListingPhoto + AI result/version |
| transport routes | async on active candidate/destination change | TravelTimeSnapshot with TTL |

Не считать весь рынок при открытии страницы. Индексы: `(source, external_id)`, active/visible + geo/rooms/area/price_per_sqm, `property_id`, event `(user, occurred_at)`, PostGIS GIST. Для PostgreSQL materialized view или aggregate tables обновлять инкрементально; invalidation — по затронутому microdistrict/room/area bucket.

## 16. Risks

- Ошибочное duplicate merge испортит пользовательское решение и цену; нужен reversible match и ручная модерация.
- Геокодер Nominatim имеет rate limits и точность street-level; hard zone rules нельзя применять к unknown как к точному адресу.
- Listing source statuses не равны продаже; `unavailable` и `sold` должны быть разными состояниями.
- Предложенная цена — не цена сделки; Deal Score и торг должны явно говорить об observational uncertainty.
- Изменение координат/микрорайона задним числом меняет market series; snapshots должны хранить evidence version.
- AI по фото может быть biased и юридически/продуктово чувствителен; только advisory label, confidence и manual override.
- Отсутствие Celery означает риск конкурирующих systemd jobs; нужны advisory locks/idempotency.
- Рост запросов comparable/duplicate без индексов может перегрузить PostgreSQL.
- Хранение локальных фотографий требует retention, privacy и copyright policy.

## 17. Implementation roadmap

Оценка сложности относительная для текущего проекта; `S` — до недели, `M` — 1–2 недели, `L` — 2–4 недели, `XL` — более 4 недель.

### P0. Основы персонального решения (рекомендуемый порядок)

#### P0.1 Географические зоны — L

- Цель: пользовательские A/B/C/D polygons, D hard exclusion.
- Backend: `GeoZone`, spatial predicate service, eligibility API; адаптер из `ScoringPreference.preferred_districts`.
- Frontend: `scoring_settings.html`, `microdistrict_map.html`/новая `/zones/`, legend and draw/edit.
- DB/migration: PostGIS Point/Polygon (или временный JSONB), GIST index, backfill coordinates.
- Jobs: geocode retry, classify listing-to-zone on coordinate/zone changes.
- Tests: polygon boundary, unknown coordinates, overlapping tiers, D exclusion.
- Dependencies: geocoding accuracy; risk — current coordinates mostly CIAN detail/Nominatim.
- Files: `models.py`, `views.py`, `urls.py`, `services/geocoding.py`, `services/microdistrict_polygons.py`, templates/map.

#### P0.2 Fit Score — L

- Цель: hard eligibility + explainable user fit.
- Backend: `scoring/fit.py`, `ScoreSnapshot`, UserPreference adapter; remove duplicate formula from views/Telegram.
- Frontend: feed/detail badges and breakdown; settings for hard/soft rules.
- DB: preference migration and score snapshots.
- Jobs: recalculate affected user/listings after ingestion/preference change.
- Tests: hard zone/area/rooms, unknown policy, component weights, deterministic versioning.
- Risks: weight tuning and sparse attributes; dependency — GeoZone.
- Files: `views.py`, `services/deal_alerts.py`, `models.py`, `templates/feed.html`, `detail.html`, `scoring_settings.html`.

#### P0.3 Deal Score + comparable engine — XL

- Цель: strict-first market value and transparent fallback.
- Backend: `ComparableSet`, `scoring/deal.py`, robust median/percentiles, versioned evidence.
- Frontend: Deal card, strict/fallback disclosure, price history/competition.
- DB: comparable indexes/materialized aggregates.
- Jobs: incremental recalc by affected geo segment.
- Tests: 5 strict rule, expansion order, outliers, missing data, no leakage between classes.
- Risks: sample bias and request-time regression; dependency — data-quality rules.
- Files: current `add_market_position`, `similar_listings`, `deal_alerts.py`, `market_export.py`, templates.

#### P0.4 «Что изменилось» — M

- Цель: one idempotent event feed since last visit.
- Backend: `ListingEvent`, event producer from persistence/scans/detail poll and score jobs, cursor endpoint.
- Frontend: `/changes/`, dashboard summary and read/unread.
- DB: unique dedupe key/index.
- Jobs: backfill from snapshots/PriceHistory; async score-change events.
- Tests: new/drop/remove/reactivate, duplicate ingestion, cursor, threshold crossing.
- Risks: event spam; dependency — stable Fit/Deal version.
- Files: `persistence.py`, `scans.py`, `poll_cian_details.py`, `change_history.py`, `views.py`, templates.

### P1. Trust, identity and workflow

#### P1.1 Property/Listing and duplicate detection — XL

- Цель: safely group cross-source listings.
- Backend: `Property`, `DuplicateMatch`, blocking/scoring service and moderation endpoints.
- Frontend: duplicate review queue, property page, grouped source cards.
- DB: nullable FK, pair unique constraint, backfill tables.
- Jobs: batch matching after import and re-match on changed address/photo.
- Tests: confirmed/probable/possible/not duplicate, reversibility, source conflicts.
- Dependencies: P0 evidence; risk — merge errors.
- Files: all listing query paths, models, views, templates, export, tests.

#### P1.2 Confidence Score — M

- Цель: evidence-backed reliability independent of attractiveness.
- Backend: confidence calculator using comparable/data/location/history/duplicate evidence and DataQualityIssue.
- Frontend: percentage + reason list and low-confidence warning.
- DB: ScoreSnapshot/issue links.
- Jobs: recompute when evidence changes.
- Tests: monotonicity (more evidence does not lower absent penalty), anomaly caps, reproducibility.
- Dependencies: comparable/duplicate models.

#### P1.3 Financial scenarios — M

- Цель: show affordability without automatic credit recommendation.
- Backend: `FinancialProfile`, pure scenario calculator current/−3/−5/−7/custom.
- Frontend: detail/finance panel with own funds, reserve, available and gap.
- DB/migration: user profile one-to-one.
- Jobs: none; calculate on demand or cache by price/profile version.
- Tests: reserve cannot be spent, negative/zero values, rounding, custom price.
- Files: `models.py`, `urls.py`, detail/new finance template, export optional.

#### P1.4 Candidate lifecycle — M

- Цель: workflow independent from rating and scores.
- Backend: `CandidateState`, transition validation/audit; migrate `Consideration`.
- Frontend: shortlist kanban/select, preserve rating 1–10 and Fit/Deal/Confidence.
- DB: status/timestamps/history; dual-write during migration.
- Jobs: automatic `removed/sold` signals only from source evidence, never infer sale silently.
- Tests: legal transitions, user isolation, review compatibility.
- Files: `models.py`, review/consideration views/templates, export.

### P2. Dossier and decision support

#### P2.1 Viewing dossier — L

- Goal: structured inspection results and user photos.
- Backend/DB: `Viewing`, observation schema, `ViewingPhoto`, permissions and audit.
- Frontend: property tabs and form with enums + notes.
- Jobs: image storage/thumbnailing only; tests for autosave and candidate access.
- Risks: privacy and unstructured data explosion.

#### P2.2 Negotiation assistant data — M

- Goal: explainable start/target/max offer inputs, not an opaque LLM answer.
- Backend: aggregator from current/original price, cuts, exposure, comparables, defects, duplicates and cross-source prices; store calculation evidence/version.
- Frontend: negotiation tab with editable assumptions and source links.
- Tests: deterministic outputs and no unsupported discount claims.
- Dependencies: P0 comparable, P1 duplicate, P2 viewing.

#### P2.3 AI photo analysis — L/XL

- Goal: labels (move-in ready/cosmetic/medium/major repair), confidence and manual override.
- Backend: `ListingPhoto`, fetch/hash pipeline, `PhotoAnalysis` version/model/confidence/override.
- Frontend: gallery labels and correction controls.
- Jobs: rate-limited download/inference; never block ingestion.
- Tests: URL changes, duplicate photo hash, retry, permission, model version.
- Risks: copyright, privacy, model bias; no ruble repair estimate.

### P3. Transport accessibility — L

- Goal: user destinations and cached car/transit/walking times.
- Backend: `TransportDestination`, provider adapter, `TravelTimeSnapshot` TTL/cache key.
- Frontend: destination editor and property travel cards.
- DB: coordinates and route cache.
- Jobs: calculate only active shortlist/new high-fit candidates, not every listing; refresh on TTL/provider changes.
- Tests: provider timeout, stale cache, mode/destination isolation.
- Risks: API cost, routing variability, privacy. External API is intentionally not connected in this analysis.

## 18. Final recommendation

Не начинать с AI и торга. Первый вертикальный срез должен пройти путь «зона → hard eligibility → Fit → strict comparables → Deal → Confidence → event». Он использует уже существующие координаты, историю цен, snapshots, market code и review data, но убирает смешение смыслов. После стабилизации этого слоя `Property`/`Listing` и остальные функции будут добавляться без повторного переписывания feed, detail, shortlist и XLSX.
