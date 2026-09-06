# Сбор ЦИАН и туннель между VPS

## Что делает приложение

`collect_listings --source cian` обходит включённые SearchQuery с source=CIAN.
Один запуск использует один Chromium с постоянным профилем; поиски идут
последовательно. Новые объявления появляются в Listing, цена — в PriceHistory,
изменения значимых полей — в ListingSnapshot. Повторный неизменившийся результат
обновляет last_seen_at без дублей истории. У каждого объявления ключ
`(source, external_id)`, поэтому ID ЦИАН не пересекаются с Avito.

Основной парсер читает JSON из inline script
`window._cianConfig['frontend-serp']`, элемент `key=initialState`,
`value.results.offers`. Это JSON-декодирование, не выполнение JavaScript.
В ответе также проверяются totalOffers, queryString и ссылка следующей страницы.
Извлекаются ID, канонический URL без аналитических параметров, заголовок,
рублёвая цена, цена/м², комнаты, площадь Decimal, этаж, этажность, адрес, район,
описание, текст даты и URL фото. Фото отдельно не скачиваются.

Заголовок берётся из formattedFullInfo с запасным вариантом из DOM.
JSON-поле title может быть null. Цена/м² читается из DOM; ID и доступные цены
сверяются с карточками. Профили, токены, телефоны продавцов и весь initialState
в БД не переносятся. Неизвестная разметка не считается пустой выдачей.

Fast по умолчанию читает одну страницу и принудительно задаёт
`sort=creation_date_desc` (новые сначала), сохраняя остальные фильтры.
Число страниц задаёт CIAN_FAST_SCAN_PAGES. Fast не деактивирует объявления и
не гарантирует обнаружение всех новых, если между запусками их больше бюджета
или сайт продвигает/переставляет карточки. Назначение — быстрый мониторинг.

Full сохраняет сортировку URL, идёт по страницам p=1,2,… и проверяет следующий
URL на сохранение фильтров. Полный успех требует конца пагинации и количества
уникальных ID не меньше максимального totalOffers, наблюдавшегося в проходе.
При несовпадении счётчиков, повреждённых карточках, смене фильтров, повторе страницы,
достижении лимита или сетевой ошибке результаты уже прочитанных страниц сохраняются,
но отсутствующие объявления не деактивируются. totalOffers может включать
группировки: консервативная проверка в таком случае оставит scan неполным.
Full отключён по умолчанию и не стоит в расписании.

## Схема соединения

```text
VPS приложения 188.120.244.43
  cian-collector: Chromium + Xvfb + Django
       │ SOCKS5, cian-tunnel:1080 (внутренняя сеть Docker)
       ▼
  cian-tunnel: OpenSSH client
       │ зашифрованное SSH-соединение TCP/22, отдельный ключ
       ▼
VPS выхода 81.19.139.155
  sshd, пользователь cian-tunnel
       │ HTTPS TCP/443 к запрошенным сайтам
       ▼
  ЦИАН (видит внешний адрес второго VPS)
```

Chromium выполняется на первом VPS. Xvfb предоставляет виртуальный экран,
поэтому headless=False работает без монитора/рабочего стола. Ноутбук не нужен.
На втором VPS не запускаются браузер и Django: sshd открывает исходящие
TCP-соединения по указанию SOCKS-клиента. При передаче имени сайта в SOCKS5
его разрешает удалённая сторона. HTTPS между браузером и сайтом сохраняется;
SSH дополнительно шифрует участок между серверами. Туннель не расшифровывает HTTPS.

Внешний адрес второго VPS отличается от адреса приложения. Во время диагностики
ЦИАН возвращал 403 при прямом выходе с первого VPS и 200 через второй.
Это подтверждённый рабочий маршрут, не гарантия отсутствия будущих блокировок.

Порт 1080 не публикуется на хост: SOCKS доступен контейнерам общей Docker-сети,
а не всему интернету. В production не используются --network host и root SSH.
Ключ находится на первом VPS в /etc/home-hunter/cian-ssh, монтируется только
в cian-tunnel read-only. Контейнерный пользователь app (UID 10001) должен иметь
право читать ключ. Приватный ключ и пароли не входят в image, Git или .env.
Ключ хоста второго VPS закреплён в known_hosts, StrictHostKeyChecking=yes.

На втором VPS выделен пользователь cian-tunnel. sshd разрешает ему только
public-key authentication и local TCP forwarding к портам 443/80; запрещены
PTY, agent/X11 forwarding, remote forwarding и выполнение shell-команд.
Конфигурация: deploy/cian-sshd.conf, активный путь
/etc/ssh/sshd_config.d/60-home-hunter-cian.conf. authorized_keys дополнительно
использует restrict,port-forwarding,command="/bin/false". Остальные пользователи
и сервисы второго VPS продолжают работать с прежними настройками.

## Отказы и восстановление

SSH работает с ServerAliveInterval=30 и ServerAliveCountMax=3. Если соединение
перестаёт отвечать, SSH завершится примерно после трёх проверок; Docker
restart: unless-stopped запустит его снова. ExitOnForwardFailure=yes не позволяет
оставить процесс без работающего локального SOCKS listener.
Healthcheck проверяет SOCKS5 greeting локально, без обращений к ЦИАН; это не
проверка доступности самого сайта. Коллектор зависит от healthy-туннеля.

CIAN_PROXY_URL обязателен. Автоматического переключения на прямое подключение
нет. При отказе прокси текущий Scan становится failed; оставшиеся поиски этого
запуска не открываются. Новый плановый запуск попробует снова.

При HTTP 403/429/439, тексте WAF или капче (даже HTTP 200) сбор прекращается.
Пауза CIAN_BLOCK_COOLDOWN_SECONDS, по умолчанию час, сохраняется в volume;
Retry-After может увеличить её. До конца паузы новые запросы не отправляются.
Автоматического решения капчи нет. Диагностика — last-response.json в профиле,
логи — Docker/systemd, итог — Scan в Admin. Сетевая ошибка не означает удаление
объявлений. Новые параметры поиска не меняют прошлые данные мгновенно.

Минимальный интервал между поисковыми переходами — 120 секунд, включая разные
поиски и разные запуски. Это осторожное рабочее значение, не официальный лимит
ЦИАН; фоновые запросы самой страницы браузер делает самостоятельно.

## Настройки

| Переменная | По умолчанию | Назначение |
|---|---|---|
| CIAN_PROXY_URL | обязательно; Compose задаёт socks5://cian-tunnel:1080 | Явный прокси |
| CIAN_STATE_DIR | .cian-state | Профиль, паузы, диагностика |
| CIAN_FAST_SCAN_PAGES | 1 | Бюджет fast |
| CIAN_MAX_PAGES | 50 | Максимум full |
| CIAN_PAGE_DELAY_SECONDS | 120 | Интервал навигаций |
| CIAN_BLOCK_COOLDOWN_SECONDS | 3600 | Пауза после блокировки |
| CIAN_FULL_SCAN_ENABLED | false | Разрешение full |
| CIAN_SSH_DESTINATION | cian-tunnel@81.19.139.155 | Адрес SSH |
| CIAN_SSH_DIR | /etc/home-hunter/cian-ssh | Каталог ключа и known_hosts |

Профиль в Docker volume cian_state, путь /app/.cian-state. Копировать его можно
только при закрытых браузерах. Один профиль нельзя открывать двумя Chromium.
Команда использует общий PostgreSQL advisory lock с Avito, systemd — общий flock.

## Управление на первом VPS

Из /opt/home_hunter:

```sh
docker compose --profile cian up -d cian-tunnel
docker compose --profile cian ps cian-tunnel
docker compose logs --tail 50 cian-tunnel
# Один fast scan, с Xvfb и всеми настроенными зависимостями:
docker compose --profile cian run --rm cian-collector
# Полный проход только явно:
docker compose --profile cian run --rm -e CIAN_FULL_SCAN_ENABLED=true cian-collector \
  xvfb-run -a -s '-screen 0 1440x1000x24 -nolisten tcp' \
  python manage.py collect_listings --source cian --mode full --headed
```

Установить units из deploy/systemd/home-hunter-cian-fast.* в /etc/systemd/system.
Таймер запускает fast каждый час с разбросом до 5 минут. Persistent=true позволяет
догнать пропущенное срабатывание после простоя. Таймаут задания 30 минут.

```sh
systemctl daemon-reload
systemctl enable --now home-hunter-cian-fast.timer
systemctl list-timers home-hunter-cian-fast.timer
journalctl -u home-hunter-cian-fast.service --since today
# Пауза новых запусков:
systemctl disable --now home-hunter-cian-fast.timer
# Остановить туннель после завершения текущего scan:
docker compose stop cian-tunnel
```

В Admin создать/редактировать SearchQuery с source=CIAN, enabled и URL фильтра.
Фильтр пользователя — вторичка, 2/3 комнаты, 5–11,5 млн, площадь от 55 м²,
дом от 9 этажей, квартира не на первом/последнем этаже. Существующие Avito timers
независимы: включение ЦИАН не включает Avito.

## Повторное развёртывание

После размещения кода, ключа, known_hosts и настройки пользователя второго VPS:

```sh
docker compose --profile cian config --quiet
docker compose --profile cian build web collector cian-collector cian-tunnel
docker compose up -d web
docker compose --profile cian up -d cian-tunnel
docker compose exec web python manage.py check
```

Новых моделей/миграций для ЦИАН нет: source=cian уже существовал в схеме.
Не удалять volumes с БД и профилями. Для восстановления старой версии сначала
остановить timer, сохранить данные и вернуть предыдущий код/image; изменение
истории объявлений не откатывать удалением БД.

## Первичная настройка SSH-доступа

На VPS приложения создать отдельный ключ (не копировать root-ключ):

```sh
install -d -o 10001 -g 10001 -m 700 /etc/home-hunter/cian-ssh
ssh-keygen -t ed25519 -N '' -C home-hunter-cian-tunnel \
  -f /etc/home-hunter/cian-ssh/id_ed25519
chown 10001:10001 /etc/home-hunter/cian-ssh/id_ed25519*
```

На втором VPS создать пользователя cian-tunnel с shell /usr/sbin/nologin,
поместить публичный ключ в /home/cian-tunnel/.ssh/authorized_keys с префиксом
`restrict,port-forwarding,command="/bin/false"`. Права каталога .ssh — 700,
файла — 600, владелец cian-tunnel. Установить deploy/cian-sshd.conf в
/etc/ssh/sshd_config.d/60-home-hunter-cian.conf, выполнить `sshd -t` и только
при успехе `systemctl reload ssh`. Проверить применённые ограничения через
`sshd -T -C user=cian-tunnel,host=81.19.139.155,addr=188.120.244.43`.

Публичный ключ хоста /etc/ssh/ssh_host_ed25519_key.pub получить через доверенную
административную сессию второго VPS. В known_hosts первого записать
`81.19.139.155 ssh-ed25519 <публичный ключ хоста>`, владелец UID 10001, mode 600.
Так доверие к SSH-хосту закрепляется явно, а не отключением проверки ключей.
При повторной установке существующие рабочие ключи не перегенерировать.

## Подтверждённое развёртывание 7 сентября 2026

Код развёрнут на 188.120.244.43. Постоянный SSH-туннель идёт к
cian-tunnel@81.19.139.155. Контейнер home_hunter-cian-tunnel-1 healthy,
опубликованных портов нет. На втором VPS проверена эффективная конфигурация sshd;
ранее работавшие telemt, amnezia-dns, amnezia-awg2 и rss_bot продолжают работать.

SearchQuery №3 содержит исходный фильтр владельца. Первый systemd-запуск:
Scan №5, success, одна страница, 27 новых объявлений. В БД подтверждены
27 Listing, 27 PriceHistory и 27 ListingSnapshot для ЦИАН. Это первая страница
с сортировкой по дате, а не загрузка всех 641 результатов поиска.

Таймер home-hunter-cian-fast.timer enabled/active. Avito timers остались
inactive/disabled. Веб-приложение и PostgreSQL работают, Admin отвечает 302
на страницу входа. Проверка миграций прошла, новых миграций нет.

Проверено восстановление: SSH-процесс внутри tunnel-контейнера завершён SIGTERM,
Docker сам перезапустил контейнер (RestartCount 0 → 1), health вернулся healthy.
Отключение сети на длительное время не моделировалось; здесь проверена реакция
на завершение SSH-процесса. Полный проход 641 объявления не запускался.

Локальные проверки: 68 tests passed, 3 необязательных browser tests skipped;
парсер дополнительно проверен на реальном сохранённом HTML с 28 объявлениями.
Новая интеграционная проверка выполняет два сбора ЦИАН на fixture и подтверждает
отсутствие дублей Listing/PriceHistory/ListingSnapshot. Production-сбор выполнен
настоящим Chromium внутри Docker/Xvfb через постоянный tunnel-контейнер.

Исходники размещены overlay из проверенного локального рабочего дерева,
публикация в GitHub не выполнялась. До обновления серверные изменяемые файлы
сохранены в /root/home-hunter-before-cian.tar.gz. Результат сборки образов:
/tmp/home-hunter-cian-build.log. Перед будущим git pull следует сохранить/согласовать
эти изменения рабочего дерева сервера; не делать git reset --hard поверх них.
