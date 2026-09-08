from listings.models import Scan, SearchQuery, SourcePollingControl


def default_polling_enabled(source: str, mode: str) -> bool:
    """Avito full scans are unsafe until a dedicated safe implementation exists."""
    return not (source == SearchQuery.Source.AVITO and mode == Scan.Mode.FULL)


def polling_enabled(source: str, mode: str = Scan.Mode.FAST) -> bool:
    control, _ = SourcePollingControl.objects.get_or_create(
        source=source, mode=mode, defaults={"enabled": default_polling_enabled(source, mode)},
    )
    return control.enabled
