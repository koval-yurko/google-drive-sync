from photolib.drive.client import DriveFile
from photolib.enrich import enrichment_for


class _Geocoder:
    enabled = True

    def __init__(self, answer="Greece"):
        self.answer = answer
        self.calls = []

    def lookup(self, lat, lon):
        self.calls.append((lat, lon))
        return self.answer


def _file(**kwargs) -> DriveFile:
    base = {"id": "d1", "name": "a.heic", "mimeType": "image/heic"}
    return DriveFile(**{**base, **kwargs})


def test_exif_time_is_the_preferred_date():
    file = _file(
        imageMediaMetadata={"time": "2025:07:14 10:30:00"},
        createdTime="2026-01-01T00:00:00Z",
    )
    result = enrichment_for(file, None)
    assert result.metadata_source == "exif"
    assert result.capture_hint == 1752489000


def test_file_time_is_the_fallback():
    file = _file(createdTime="2026-01-01T00:00:00Z")
    result = enrichment_for(file, None)
    assert result.metadata_source == "file_time"
    assert result.capture_hint is not None


def test_no_date_at_all():
    result = enrichment_for(_file(), None)
    assert result.metadata_source == "none"
    assert result.capture_hint is None


def test_gps_becomes_a_country():
    geocoder = _Geocoder()
    file = _file(imageMediaMetadata={"location": {"latitude": 37.9,
                                                  "longitude": 23.7}})
    result = enrichment_for(file, geocoder)
    assert (result.latitude, result.longitude) == (37.9, 23.7)
    assert result.country == "Greece"
    assert geocoder.calls == [(37.9, 23.7)]


def test_no_gps_never_calls_the_geocoder():
    geocoder = _Geocoder()
    enrichment_for(_file(), geocoder)
    assert geocoder.calls == []


def test_tag_properties_become_slugs():
    file = _file(appProperties={"t_family": "1", "t_greece-2025": "1",
                                "source_crc": "123"})
    result = enrichment_for(file, None)
    assert sorted(result.tag_slugs) == ["family", "greece-2025"]


def test_no_tag_properties_is_an_empty_list():
    assert enrichment_for(_file(appProperties={"country": "GR"}), None).tag_slugs == []
