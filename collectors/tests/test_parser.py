from decimal import Decimal
from pathlib import Path
import pytest
from collectors.avito.parser import parse_page, parse_title

FIXTURE = Path(__file__).parent / "fixtures/search.html"


@pytest.mark.parametrize("title,rooms,area,floor,total", [
    ("2-к. квартира, 58,6 м², 6/12 эт.", 2, "58.6", 6, 12),
    ("Аукцион: 3-к. квартира, 72,9 м², 7/9 эт.", 3, "72.9", 7, 9),
])
def test_title(title, rooms, area, floor, total):
    assert parse_title(title) == dict(rooms=rooms, area=Decimal(area), floor=floor, floors_total=total)


def test_unknown_title(caplog):
    assert all(value is None for value in parse_title("Неизвестный формат").values())
    assert "Unknown title" in caplog.text


def test_cards():
    result = parse_page(FIXTURE.read_text())
    assert (result.card_count, result.errors, len(result.listings)) == (3, 1, 2)
    first, second = result.listings
    assert first.external_id == "123"
    assert first.price == 6500000
    assert first.price_per_sqm == 110922
    assert first.url == "https://www.avito.ru/ufa/kvartiry/flat_123"
    assert first.address == "Уфа, ул. Ленина, 1"
    assert first.district == "Кировский"
    assert first.description == "Светлая квартира"
    assert first.image_url == "https://images.example/1.jpg"
    assert first.published_text == "1 час назад"
    assert second.published_text is None
    assert second.price == 8000000


def test_district_falls_back_to_address():
    html = '''<div data-marker="item" data-item-id="1">
      <a data-marker="item-title" href="/1">2-к. квартира, 50 м², 3/9 эт.</a>
      <span data-marker="item-address">ул. Ленина, 1 · 4,7 · 9 отзывов р-н Кировский</span>
    </div>'''
    assert parse_page(html).listings[0].district == "Кировский"


def test_date_does_not_change_fallback_description():
    html = '<div data-marker="item" data-item-id="1"><a data-marker="item-title" href="/1">Flat</a><span data-marker="item-date">{}</span></div>'
    a = parse_page(html.format("Вчера")).listings[0]
    b = parse_page(html.format("3 дня назад")).listings[0]
    assert a.description == b.description
