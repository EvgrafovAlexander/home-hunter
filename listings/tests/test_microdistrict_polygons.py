from listings.services.microdistrict_polygons import (
    canonical_microdistrict_name, load_polygons, normalized_microdistrict_label, resolve_microdistrict,
)


def test_cached_ufa_polygon_catalog_is_complete():
    polygons = load_polygons()
    assert len(polygons) == 47
    assert len({item["id"] for item in polygons}) == 47
    assert all(item["geometry_type"] == "Polygon" for item in polygons)


def test_polygon_resolver_matches_a_polygon_vertex():
    polygon = load_polygons()[0]
    latitude, longitude = polygon["coordinates"][0][0]
    result = resolve_microdistrict(latitude, longitude)
    assert result is not None
    assert result.polygon_id in {item["id"] for item in load_polygons()}
    assert result.confidence == 0.85


def test_polygon_resolver_returns_none_outside_ufa():
    assert resolve_microdistrict(0, 0) is None


def test_polygon_names_are_canonicalized_for_directory_matching():
    assert canonical_microdistrict_name("Южный м-н") == "южный"
    assert canonical_microdistrict_name("Иремель жк") == "иремель"


def test_normalized_label_removes_source_suffixes_and_nbsp():
    assert normalized_microdistrict_label("Иремель\xa0жк") == "Иремель"
    assert normalized_microdistrict_label("Мечта пос.") == "Мечта"
