def get_collector(source: str, *, headless: bool = True, manual_wait_seconds: float = 0, profile=None):
    route = profile.route if profile is not None else None
    session_mode = profile.session_mode if profile is not None else "settings"
    if source == "avito":
        from collectors.avito.collector import AvitoCollector
        return AvitoCollector(headless=headless, manual_wait_seconds=manual_wait_seconds,
                              route=route, session_mode=session_mode)
    if source == "cian":
        from collectors.cian.collector import CianCollector
        return CianCollector(headless=headless, manual_wait_seconds=manual_wait_seconds,
                             route=route, session_mode=session_mode)
    if source == "domclick":
        from collectors.domclick.collector import DomclickCollector
        return DomclickCollector(headless=headless, manual_wait_seconds=manual_wait_seconds,
                                 route=route, session_mode=session_mode)
    raise ValueError(f"Unsupported collector: {source}")
