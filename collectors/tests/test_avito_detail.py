from collectors.avito.detail import parse_detail_payload


def test_detail_extracts_coordinates_gallery_and_views():
    detail = parse_detail_payload({
        "success": {"view": {"location": {"latitude": 56.8, "longitude": 60.6},
                                "gallery": {"images": [{"640x480": "https://cdn.test/1.jpg"}]}},
                    "mobile": {"stats": {"views": 41}}},
    })
    assert detail.status == "published"
    assert str(detail.latitude) == "56.8"
    assert detail.photos == [{"full_url": "https://cdn.test/1.jpg"}]
    assert detail.views == 41


def test_detail_false_success_is_unavailable():
    assert parse_detail_payload({"success": False}).status == "unavailable"
