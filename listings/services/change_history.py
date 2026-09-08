"""Present stored snapshots as a concise, human-readable change log."""

from decimal import Decimal, InvalidOperation


def _number(value, suffix=""):
    if value in (None, ""):
        return "не указано"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    rendered = f"{number:,.2f}".replace(",", " ").rstrip("0").rstrip(".")
    return f"{rendered}{suffix}"


def _text(value):
    return str(value) if value not in (None, "") else "не указано"


def _photo_change(before, after):
    if not before and after:
        return "Фото добавлено"
    if before and not after:
        return "Фото удалено"
    return "Главное фото обновлено"


def _description_change(before, after):
    if not before and after:
        return "Описание добавлено"
    if before and not after:
        return "Описание удалено"
    return "Описание обновлено"


def _field_change(field, before, after):
    if field == "price":
        return {"label": "Цена", "before": _number(before, " ₽"), "after": _number(after, " ₽")}
    if field == "price_per_sqm":
        return {"label": "Цена за м²", "before": _number(before, " ₽/м²"), "after": _number(after, " ₽/м²")}
    if field == "area":
        return {"label": "Площадь", "before": _number(before, " м²"), "after": _number(after, " м²")}
    if field == "rooms":
        return {"label": "Комнат", "before": _text(before), "after": _text(after)}
    if field == "floor":
        return {"label": "Этаж", "before": _text(before), "after": _text(after)}
    if field == "floors_total":
        return {"label": "Этажей в доме", "before": _text(before), "after": _text(after)}
    if field == "image_url":
        return {"label": _photo_change(before, after), "before": None, "after": None}
    if field == "description":
        return {"label": _description_change(before, after), "before": None, "after": None}
    if field == "is_active":
        return {"label": "Статус", "before": "Активно" if before else "Снято с публикации",
                "after": "Активно" if after else "Снято с публикации"}
    labels = {
        "title": "Заголовок", "address": "Адрес", "district": "Район", "microdistrict": "Микрорайон",
        "published_text": "Дата публикации", "is_visible": "Показывать в подборке",
    }
    if field == "is_visible":
        return {"label": labels[field], "before": "Да" if before else "Нет", "after": "Да" if after else "Нет"}
    return {"label": labels[field], "before": _text(before), "after": _text(after)}


TRACKED_FIELDS = (
    "price", "price_per_sqm", "rooms", "area", "floor", "floors_total", "address", "district",
    "microdistrict", "title", "published_text", "image_url", "description", "is_visible", "is_active",
)


def change_events(snapshots):
    """Return newest-first events, omitting the initial snapshot's field noise."""
    previous = None
    events = []
    for snapshot in reversed(snapshots):
        data = snapshot.data or {}
        if previous is None:
            events.append({"observed_at": snapshot.observed_at, "initial": True, "changes": []})
        else:
            changes = [
                _field_change(field, previous.get(field), data.get(field))
                for field in TRACKED_FIELDS if previous.get(field) != data.get(field)
            ]
            if changes:
                events.append({"observed_at": snapshot.observed_at, "initial": False, "changes": changes})
        previous = data
    return list(reversed(events))
