def get_collector(source: str, *, headless: bool = True, manual_wait_seconds: float = 0):
    if source == "avito":
        from collectors.avito.collector import AvitoCollector
        return AvitoCollector(headless=headless, manual_wait_seconds=manual_wait_seconds)
    raise ValueError(f"Unsupported collector: {source}")
