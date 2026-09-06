def get_collector(source: str, *, headless: bool = True):
    if source == "avito":
        from collectors.avito.collector import AvitoCollector
        return AvitoCollector(headless=headless)
    raise ValueError(f"Unsupported collector: {source}")
