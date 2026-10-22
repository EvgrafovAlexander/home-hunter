# Правила для collectors

- Parser-функции должны быть детерминированными и не обращаться к ORM, сети или файловой системе.
- Collector возвращает `NormalizedListing`/`CollectionResult`; persistence выполняется в `listings.services`.
- Нельзя импортировать `listings.views` или вызывать Django views из collector-кода.
- Browser, Playwright context, proxy и session state всегда закрываются в успешном и ошибочном сценарии.
- Block, cooldown, повреждённая карточка и partial pagination обрабатываются fail-closed.
- Изменение селекторов сопровождается fixture-тестом; live Avito/CIAN/DomClick не используется в обычном CI.
- Credentials и cookies не попадают в логи, diagnostics и исключения.
