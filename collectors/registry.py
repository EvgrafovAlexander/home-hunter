def get_collector(source: str, *, headless: bool = True, manual_wait_seconds: float = 0):
    if source == "avito":
        from collectors.avito.collector import AvitoCollector
        return AvitoCollector(headless=headless, manual_wait_seconds=manual_wait_seconds)
    if source == "cian":
        from collectors.cian.collector import CianCollector
        return CianCollector(headless=headless, manual_wait_seconds=manual_wait_seconds)
    if source == "domclick":
        from collectors.domclick.collector import DomclickCollector
        return DomclickCollector(headless=headless, manual_wait_seconds=manual_wait_seconds)
    raise ValueError(f"Unsupported collector: {source}")
