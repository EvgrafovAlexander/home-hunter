"""Build the current market workbook without requiring a desktop spreadsheet package."""

from datetime import date, datetime
from decimal import Decimal
from html import escape
from io import BytesIO
from statistics import median
from zipfile import ZIP_DEFLATED, ZipFile

from django.utils import timezone

from ..models import CianDetailPollState, Consideration, Listing, ListingReview, ListingSearchQuery, PriceHistory, SearchQuery


def _text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    return str(value)


def _excel_date(value):
    if not value:
        return None
    if timezone.is_aware(value):
        value = timezone.make_naive(value)
    return (value - datetime(1899, 12, 30)).total_seconds() / 86400


def _cell(value, style=0):
    if value is None or value == "":
        return ""
    if isinstance(value, (datetime, date)):
        number = _excel_date(datetime.combine(value, datetime.min.time()) if isinstance(value, date) and not isinstance(value, datetime) else value)
        return f'<c s="2" t="n"><v>{number}</v></c>'
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c s="3" t="n"><v>{value}</v></c>'
    return f'<c s="{style}" t="inlineStr"><is><t>{escape(_text(value))}</t></is></c>'


def _col_name(index):
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _sheet_xml(rows, widths):
    last_col = _col_name(max(1, len(widths)))
    body = []
    for row_index, row in enumerate(rows, 1):
        cells = "".join(_cell(value, style=1 if row_index == 1 else 0) for value in row)
        body.append(f'<row r="{row_index}">{cells}</row>')
    cols = "".join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i, width in enumerate(widths, 1))
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:{last_col}{len(rows)}"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/><cols>{cols}</cols><sheetData>{''.join(body)}</sheetData><autoFilter ref="A1:{last_col}{len(rows)}"/></worksheet>'''


def _workbook_xml(names):
    sheets = "".join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(names, 1))
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{sheets}</sheets></workbook>'''


def _styles_xml():
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="2"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm"/><numFmt numFmtId="165" formatCode="#,##0.0"/></numFmts><fonts count="2"><font><sz val="10"/><name val="Arial"/><color rgb="FF222222"/></font><font><b/><sz val="10"/><name val="Arial"/><color rgb="FFFFFFFF"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/><xf numFmtId="0" fontId="1" fillId="1" borderId="0" applyFont="1" applyFill="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0"/></cellXfs></styleSheet>'''


def build_market_xlsx(user):
    listings = list(Listing.objects.all().order_by("id"))
    reviews = list(ListingReview.objects.filter(author=user).select_related("listing", "author").prefetch_related("tags").order_by("id"))
    considerations = {item.review_id: item for item in Consideration.objects.filter(review__author=user)}
    own_reviews = {item.listing_id: item for item in reviews if item.author_id == user.id}
    histories = list(PriceHistory.objects.select_related("listing").order_by("listing_id", "observed_at", "id"))
    states = list(CianDetailPollState.objects.select_related("listing").order_by("id"))
    relations = list(ListingSearchQuery.objects.select_related("search_query").order_by("listing_id", "id"))
    query_names = {item.id: f"{item.search_query.name} ({item.search_query.source})" for item in relations}
    relations_by_listing = {}
    for relation in relations:
        relations_by_listing.setdefault(relation.listing_id, []).append(query_names[relation.id])
    states_by_listing = {item.listing_id: item for item in states}
    source_names = dict(SearchQuery.Source.choices)
    review_decisions = dict(ListingReview.Decision.choices)
    stages = dict(Consideration.Stage.choices)

    listing_headers = ["ID", "Источник", "Внешний ID", "Заголовок", "URL", "Цена, ₽", "Цена за м², ₽", "Комнат", "Площадь, м²", "Кухня, м²", "Жилая, м²", "Этаж", "Этажность", "Год постройки", "Адрес", "Район", "Микрорайон", "Ремонт", "Материал дома", "Мебель", "Лифт", "Балкон/лоджия", "Санузлы совм.", "Санузлы раздельн.", "Парковка", "Высота потолка, м", "Статус публикации", "Активно", "Видимо", "Первое появление", "Последнее появление", "Обновлено", "Моя оценка, /10", "Моё решение", "Этап shortlist", "Комментарий", "Причина интереса", "Стоп-фактор", "CIAN detail status", "CIAN detail checked", "Поисковые запросы", "Фото URL"]
    listing_rows = [listing_headers]
    for item in listings:
        review = own_reviews.get(item.id)
        consideration = considerations.get(review.id) if review else None
        state = states_by_listing.get(item.id)
        listing_rows.append([item.id, source_names.get(item.source, item.source), item.external_id, item.title, item.url, item.price, item.price_per_sqm, item.rooms, item.area, item.kitchen_area, item.living_area, item.floor, item.floors_total, item.built_year, item.address, item.district, item.microdistrict, item.repair_type, item.building_material_type, item.has_furniture, (item.passenger_lifts_count or 0) + (item.cargo_lifts_count or 0), (item.balconies_count or 0) + (item.loggias_count or 0), item.bathrooms_combined, item.bathrooms_separate, item.parking_type, item.ceiling_height, item.get_publication_status_display(), item.is_active, item.is_visible, item.first_seen_at, item.last_seen_at, item.updated_at, review.rating if review else None, review_decisions.get(review.decision) if review else None, stages.get(consideration.stage) if consideration else None, review.comment if review else None, review.interest_reason if review else None, review.deal_breaker if review else None, state.status if state else None, state.last_checked_at if state else None, "; ".join(relations_by_listing.get(item.id, [])), item.image_url])

    review_rows = [["Review ID", "Listing ID", "Источник", "Адрес", "Автор", "Оценка, /10", "Решение", "Этап", "Комментарий", "Причина интереса", "Стоп-фактор", "Создано", "Обновлено"]]
    for review in reviews:
        consideration = considerations.get(review.id)
        review_rows.append([review.id, review.listing_id, source_names.get(review.listing.source, review.listing.source), review.listing.address, review.author.username, review.rating, review_decisions.get(review.decision, review.decision), stages.get(consideration.stage) if consideration else None, review.comment, review.interest_reason, review.deal_breaker, review.created_at, review.updated_at])

    price_rows = [["ID", "Listing ID", "Источник", "Дата", "Цена, ₽", "Цена за м², ₽", "Площадь, м²", "Изменение цены, ₽", "Изменение, %", "Источник записи"]]
    previous_prices = {}
    for item in histories:
        previous = previous_prices.get(item.listing_id)
        change = item.price - previous if item.price is not None and previous is not None else None
        change_pct = round(change / previous * 100, 2) if change is not None and previous else None
        price_rows.append([item.id, item.listing_id, source_names.get(item.listing.source, item.listing.source), item.observed_at, item.price, item.listing.price_per_sqm, item.listing.area, change, change_pct, "Наблюдение цены"])
        if item.price is not None:
            previous_prices[item.listing_id] = item.price

    cian_rows = [["State ID", "Listing ID", "Источник", "External ID", "Статус detail", "Проверено", "Последняя ошибка"]]
    for item in states:
        cian_rows.append([item.id, item.listing_id, source_names.get(item.listing.source, item.listing.source), item.listing.external_id, item.status, item.last_checked_at, item.last_error])

    market_rows = [["Район", "Микрорайон", "Объявлений", "Видимых", "Скрытых", "Медианная цена, ₽", "Медиана за м², ₽", "Медиана площади, м²"]]
    market_groups = {}
    for item in listings:
        if not item.is_active or not item.district or not item.microdistrict or item.price_per_sqm is None:
            continue
        market_groups.setdefault((item.district, item.microdistrict), []).append(item)
    for (district, microdistrict), entries in sorted(market_groups.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        if len(entries) < 5:
            continue
        prices = [item.price for item in entries if item.price is not None]
        areas = [float(item.area) for item in entries if item.area is not None]
        market_rows.append([district, microdistrict, len(entries), sum(item.is_visible for item in entries), sum(not item.is_visible for item in entries), int(median(prices)) if prices else None, int(median([item.price_per_sqm for item in entries])), median(areas) if areas else None])

    rooms_rows = [["Комнат", "Объявлений", "Видимых", "Скрытых", "Медианная цена, ₽", "Медиана за м², ₽", "Медиана площади, м²"]]
    for rooms in (2, 3):
        entries = [item for item in listings if item.is_active and item.rooms == rooms and item.price_per_sqm is not None]
        prices = [item.price for item in entries if item.price is not None]
        areas = [float(item.area) for item in entries if item.area is not None]
        rooms_rows.append([rooms, len(entries), sum(item.is_visible for item in entries), sum(not item.is_visible for item in entries), int(median(prices)) if prices else None, int(median([item.price_per_sqm for item in entries])), median(areas) if areas else None])

    summary_rows = [["Home Hunter — выгрузка рынка квартир"], [], ["Показатель", "Значение"], ["Дата выгрузки", timezone.now()], ["Квартир", len(listings)], ["Активных", sum(item.is_active for item in listings)], ["Видимых", sum(item.is_visible for item in listings)], ["Скрытых", sum(not item.is_visible for item in listings)], ["Оценок пользователя", len(reviews)], ["Готов рассмотреть", len(considerations)], ["Историй цен", len(histories)], ["CIAN detail записей", len(states)], ["Источники", ", ".join(sorted({source_names.get(item.source, item.source) for item in listings}))]]
    sheets = [("Обзор", summary_rows, [34, 24]), ("Квартиры", listing_rows, [10, 14, 18, 42, 52, 14, 14, 10, 12, 12, 12, 9, 10, 12, 36, 18, 18, 16, 18, 10, 10, 14, 14, 16, 16, 16, 20, 10, 10, 20, 20, 20, 14, 22, 22, 28, 28, 18, 20, 42, 48]), ("Оценки", review_rows, [12, 12, 14, 36, 16, 12, 22, 22, 30, 30, 30, 20, 20]), ("Цены", price_rows, [12, 12, 14, 20, 14, 14, 14, 16, 14, 24]), ("CIAN статусы", cian_rows, [12, 12, 14, 18, 18, 20, 36]), ("Рынок микрорайонов", market_rows, [18, 24, 14, 12, 12, 18, 18, 18]), ("Рынок по комнатам", rooms_rows, [10, 14, 12, 12, 18, 18, 18])]
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets) + 1)) + '</Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", _workbook_xml([name for name, _, _ in sheets]))
        archive.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1)) + '<Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        archive.writestr("xl/styles.xml", _styles_xml())
        for index, (_, rows, widths) in enumerate(sheets, 1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(rows, widths))
    return output.getvalue()
