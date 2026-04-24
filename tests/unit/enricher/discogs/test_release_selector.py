from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tagger.enricher.discogs.client import DiscogsClient
from tagger.enricher.discogs.models import DiscogsSearchResult
from tagger.enricher.discogs.release_selector import ReleaseSelector


@pytest.fixture
def mock_client() -> MagicMock:
    return MagicMock(spec=DiscogsClient)


@pytest.fixture
def selector(mock_client: DiscogsClient) -> ReleaseSelector:
    return ReleaseSelector(client=mock_client, threshold=85)


def test_find_best_match_basic(selector: ReleaseSelector, mock_client: MagicMock) -> None:
    results = [
        DiscogsSearchResult(
            id=1,
            type="release",
            master_id=10,
            title="Artist - Album",
            year=1994,
            resource_url="https://api.discogs.com/releases/1",
        ),
        DiscogsSearchResult(
            id=2,
            type="release",
            master_id=11,
            title="Wrong Artist - Different Album",
            year=1995,
            resource_url="https://api.discogs.com/releases/2",
        ),
    ]
    mock_client.search_album.return_value = results

    # We also need to mock get_master_releases for the master_id
    mock_client.get_master_releases.return_value = [results[0]]

    best_release = selector.find_best_release("Artist", "Album")

    assert best_release is not None
    assert best_release.id == 1


def test_find_best_match_no_results(selector: ReleaseSelector, mock_client: MagicMock) -> None:
    mock_client.search_album.return_value = []

    best_release = selector.find_best_release("Artist", "Album")
    assert best_release is None


def test_oldest_release_selection(selector: ReleaseSelector, mock_client: MagicMock) -> None:
    # Initial search returns a reissue
    search_results = [
        DiscogsSearchResult(
            id=2,
            type="release",
            master_id=10,
            title="Artist - Album",
            year=2000,
            resource_url="https://api.discogs.com/releases/2",
        )
    ]
    mock_client.search_album.return_value = search_results

    # Master versions has the original and another reissue
    master_versions = [
        DiscogsSearchResult(
            id=1,
            type="release",
            master_id=10,
            title="Artist - Album",
            year=1994,
            resource_url="https://api.discogs.com/releases/1",
        ),
        DiscogsSearchResult(
            id=2,
            type="release",
            master_id=10,
            title="Artist - Album",
            year=2000,
            resource_url="https://api.discogs.com/releases/2",
        ),
        DiscogsSearchResult(
            id=3,
            type="release",
            master_id=10,
            title="Artist - Album",
            year=1996,
            resource_url="https://api.discogs.com/releases/3",
        ),
    ]
    mock_client.get_master_releases.return_value = master_versions

    best_release = selector.find_best_release("Artist", "Album")

    assert best_release is not None
    assert best_release.id == 1  # Oldest one


def test_find_best_match_below_threshold(selector: ReleaseSelector, mock_client: MagicMock) -> None:
    results = [
        DiscogsSearchResult(
            id=1,
            type="release",
            master_id=10,
            title="Something Completely Different",
            year=1994,
            resource_url="https://api.discogs.com/releases/1",
        )
    ]
    mock_client.search_album.return_value = results

    best_release = selector.find_best_release("Artist", "Album")
    assert best_release is None


def test_find_best_match_no_master(selector: ReleaseSelector, mock_client: MagicMock) -> None:
    results = [
        DiscogsSearchResult(
            id=1,
            type="release",
            master_id=None,
            title="Artist - Album",
            year=1994,
            resource_url="https://api.discogs.com/releases/1",
        )
    ]
    mock_client.search_album.return_value = results

    best_release = selector.find_best_release("Artist", "Album")
    assert best_release is not None
    assert best_release.id == 1
    assert mock_client.get_master_releases.called is False


def _make_result(
    release_id: int,
    title: str = "Artist - Album",
    year: int = 1994,
    formats: list[str] | None = None,
    master_id: int | None = None,
) -> DiscogsSearchResult:
    return DiscogsSearchResult(
        id=release_id,
        type="release",
        master_id=master_id,
        title=title,
        year=year,
        format=formats or [],
        resource_url=f"https://api.discogs.com/releases/{release_id}",
    )


def test_video_format_results_excluded_from_selection(
    selector: ReleaseSelector, mock_client: MagicMock
) -> None:
    """VHS, DVD, and similar video results are filtered out before selection."""
    mock_client.search_album.return_value = [
        _make_result(1, formats=["VHS", "NTSC"]),
        _make_result(2, formats=["DVD"]),
        _make_result(3, formats=["CD", "Album"]),
    ]
    mock_client.get_master_releases.return_value = []

    result = selector.find_best_release("Artist", "Album")

    assert result is not None
    assert result.id == 3


def test_all_video_results_fall_through_to_none(
    selector: ReleaseSelector, mock_client: MagicMock
) -> None:
    """If every result is a video format, return None (both primary and fallback search)."""
    mock_client.search_album.side_effect = [
        [_make_result(1, formats=["VHS"]), _make_result(2, formats=["DVD"])],
        [],
    ]

    result = selector.find_best_release("Artist", "Album")

    assert result is None


def test_audio_formats_preferred_in_master_versions(
    selector: ReleaseSelector, mock_client: MagicMock
) -> None:
    """Video versions are skipped when picking the oldest master version."""
    mock_client.search_album.return_value = [_make_result(1, master_id=10, year=2000)]
    mock_client.get_master_releases.return_value = [
        _make_result(10, year=1998, formats=["VHS"]),
        _make_result(11, year=1998, formats=["DVD"]),
        _make_result(12, year=1998, formats=["CD", "Album"]),
        _make_result(13, year=1999, formats=["Vinyl", "LP"]),
    ]

    result = selector.find_best_release("Artist", "Album")

    assert result is not None
    assert result.id == 12  # oldest non-video version


def test_track_count_hint_selects_matching_version_over_oldest(
    selector: ReleaseSelector, mock_client: MagicMock
) -> None:
    """When track_count is given, the version whose track count matches is returned
    even if it isn't the oldest."""
    from tagger.enricher.discogs.models import DiscogsArtist, DiscogsRelease, DiscogsTrack

    mock_client.search_album.return_value = [_make_result(1, master_id=10, year=2001)]
    mock_client.get_master_releases.return_value = [
        _make_result(7, year=1998, formats=["CD"]),  # oldest but only 7 tracks
        _make_result(19, year=2001, formats=["CD"]),  # 19 tracks — correct
    ]

    def fake_get_release(release_id: int) -> DiscogsRelease:
        count = release_id  # release id encodes the track count for this test
        return DiscogsRelease(
            id=release_id,
            title="Album",
            artists=[DiscogsArtist(id=1, name="Artist")],
            tracklist=[DiscogsTrack(position=str(i), title=f"T{i}") for i in range(1, count + 1)],
            uri=f"http://discogs/{release_id}",
        )

    mock_client.get_release.side_effect = fake_get_release

    result = selector.find_best_release("Artist", "Album", track_count=19)

    assert result is not None
    assert result.id == 19  # matched on track count, not oldest


def test_track_count_hint_prefers_cd_over_cassette_same_year(
    selector: ReleaseSelector, mock_client: MagicMock
) -> None:
    """When a CD and a Cassette share the same year and track count, the CD is returned."""
    from tagger.enricher.discogs.models import DiscogsArtist, DiscogsRelease, DiscogsTrack

    mock_client.search_album.return_value = [_make_result(1, master_id=10, year=1998)]
    mock_client.get_master_releases.return_value = [
        _make_result(8, year=1998, formats=["Cassette", "Compilation"]),
        _make_result(9, year=1998, formats=["CD", "Compilation", "Club Edition"]),
    ]

    def fake_get_release(release_id: int) -> DiscogsRelease:
        return DiscogsRelease(
            id=release_id,
            title="Album",
            artists=[DiscogsArtist(id=1, name="Artist")],
            tracklist=[DiscogsTrack(position=str(i), title=f"T{i}") for i in range(1, 20)],
            uri=f"http://discogs/{release_id}",
        )

    mock_client.get_release.side_effect = fake_get_release

    result = selector.find_best_release("Artist", "Album", track_count=19)

    assert result is not None
    assert result.id == 9  # CD preferred over Cassette for same year


def test_track_count_hint_falls_back_to_oldest_when_no_version_matches(
    selector: ReleaseSelector, mock_client: MagicMock
) -> None:
    """When no version has the requested track count, oldest audio version is returned."""
    from tagger.enricher.discogs.models import DiscogsArtist, DiscogsRelease, DiscogsTrack

    mock_client.search_album.return_value = [_make_result(1, master_id=10, year=2001)]
    mock_client.get_master_releases.return_value = [
        _make_result(5, year=1998, formats=["CD"]),
        _make_result(7, year=2001, formats=["CD"]),
    ]

    def fake_get_release(release_id: int) -> DiscogsRelease:
        return DiscogsRelease(
            id=release_id,
            title="Album",
            artists=[DiscogsArtist(id=1, name="Artist")],
            tracklist=[DiscogsTrack(position=str(i), title=f"T{i}") for i in range(1, 8)],
            uri=f"http://discogs/{release_id}",
        )

    mock_client.get_release.side_effect = fake_get_release

    result = selector.find_best_release("Artist", "Album", track_count=19)

    assert result is not None
    assert result.id == 5  # oldest audio version despite no count match


def test_find_best_match_master_empty(selector: ReleaseSelector, mock_client: MagicMock) -> None:
    results = [
        DiscogsSearchResult(
            id=1,
            type="release",
            master_id=10,
            title="Artist - Album",
            year=1994,
            resource_url="https://api.discogs.com/releases/1",
        )
    ]
    mock_client.search_album.return_value = results
    mock_client.get_master_releases.return_value = []

    best_release = selector.find_best_release("Artist", "Album")
    assert best_release is not None
    assert best_release.id == 1
