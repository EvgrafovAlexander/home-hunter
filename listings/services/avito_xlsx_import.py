"""Импортирует XLSX Avito Parser в общий persistence-контракт без сетевых запросов."""

from dataclasses import dataclass, field
from decimal import Decimal
import re
from urllib.parse import parse_qs, urlsplit

from django.utils import timezone
from openpyxl import load_workbook

from collectors.base import NormalizedListing
from listings.services.persistence import process_listing


@dataclass
class ImportResult:
    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: list[str] = field(default_factory=list)


def _matches_search(value, search_url):
    source, target = urlsplit(str(value or "")), urlsplit(search_url)
    if (source.netloc, source.path) != (target.netloc, target.path):
        return False
    source_q, target_q = parse_qs(source.query), parse_qs(target.query)
    return all(source_q.get(key) == target_q.get(key) for key in ("district", "f", "s"))


def import_avito_xlsx(uploaded_file, search):
    """Сохраняет карточки выбранной выдачи и возвращает подробный журнал строк."""
    book = load_workbook(uploaded_file, read_only=True, data_only=True)
    if "Items" not in book.sheetnames:
        raise ValueError("В книге отсутствует лист Items")
    sheet = book["Items"]
    headers = [cell.value for cell in next(sheet.iter_rows())]
    if not {"itemId", "title", "url", "sourcePageUrl"}.issubset(headers):
        raise ValueError("Файл не похож на выгрузку Avito Parser")
    result = ImportResult(); observed_at = timezone.now()
    pattern = re.compile(r"(?P<rooms>\d+)-к\.\s*квартира,\s*(?P<area>\d+(?:[,.]\d+)?)\s*м[²2],\s*(?P<floor>\d+)\s*/\s*(?P<total>\d+)\s*эт\.", re.I)
    for number, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
        row = dict(zip(headers, values))
        if not row.get("itemId"): continue
        result.total += 1
        if not _matches_search(row.get("sourcePageUrl"), search.url): result.skipped.append(f"{number}: не из выбранной выдачи"); continue
        if "/kvartiry/" not in str(row.get("url") or ""): result.skipped.append(f"{number}: не квартира"); continue
        match = pattern.search(str(row.get("title") or ""))
        if not match: result.skipped.append(f"{number}: формат карточки не поддержан"); continue
        digits = re.sub(r"\D", "", str(row.get("priceValue") or row.get("price") or ""))
        item = NormalizedListing(source="avito", external_id=str(row["itemId"]), url=str(row["url"]), title=str(row["title"]), price=int(digits) if digits else None, rooms=int(match["rooms"]), area=Decimal(match["area"].replace(",", ".")), floor=int(match["floor"]), floors_total=int(match["total"]), address=row.get("location") or None, description=row.get("cardTexts") or None, published_text=row.get("dateText") or None, image_url=row.get("imageUrl") or None, raw_data={"source_page_url": row.get("sourcePageUrl")})
        saved = process_listing(search, item, observed_at); result.created += int(saved.created); result.updated += int(saved.updated)
    return result
