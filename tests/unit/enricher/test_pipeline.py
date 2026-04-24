from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tagger.db.models import AlbumRecord, TrackRecord
from tagger.enricher.discogs.models import (
    DiscogsArtist,
    DiscogsArtistDetail,
    DiscogsRelease,
    DiscogsSearchResult,
    DiscogsTrack,
    DiscogsTrackArtist,
)
from tagger.enricher.models import EnrichmentData
from tagger.enricher.pipeline import EnrichmentPipeline


@pytest.fixture
def mock_album_repo() -> MagicMock:
    repo = MagicMock()
    # Mock the internal connection context manager
    repo._conn = MagicMock()
    repo._conn.__enter__.return_value = repo._conn
    return repo


@pytest.fixture
def mock_track_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_discogs_client() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_scraper() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_enricher() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_mb_client() -> MagicMock:
    return MagicMock()


@pytest.fixture
def pipeline(
    mock_album_repo: MagicMock,
    mock_track_repo: MagicMock,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
) -> EnrichmentPipeline:
    return EnrichmentPipeline(
        album_repo=mock_album_repo,
        track_repo=mock_track_repo,
        discogs_client=mock_discogs_client,
        scraper=mock_scraper,
        enricher=mock_enricher,
    )


@pytest.fixture
def pipeline_with_mb(
    mock_album_repo: MagicMock,
    mock_track_repo: MagicMock,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_mb_client: MagicMock,
) -> EnrichmentPipeline:
    return EnrichmentPipeline(
        album_repo=mock_album_repo,
        track_repo=mock_track_repo,
        discogs_client=mock_discogs_client,
        scraper=mock_scraper,
        enricher=mock_enricher,
        mb_client=mock_mb_client,
    )


def test_enrich_album_success(
    pipeline: EnrichmentPipeline,
    mock_album_repo: MagicMock,
    mock_track_repo: MagicMock,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
) -> None:
    album_record = AlbumRecord(
        id=1, artist_guess="Artist", album_guess="Album", folder_path="/path"
    )

    # 1. Mock Discogs Search
    mock_search_result = DiscogsSearchResult(
        id=100, type="release", title="Artist - Album", resource_url="http://api/100"
    )

    pipeline._selector.find_best_release = MagicMock(return_value=mock_search_result)

    # 2. Mock Get Release
    mock_release = DiscogsRelease(
        id=100,
        title="Album",
        artists=[DiscogsArtist(id=1, name="Artist")],
        tracklist=[DiscogsTrack(position="1", title="Track 1")],
        uri="http://discogs/100",
    )
    mock_discogs_client.get_release.return_value = mock_release

    # 3. Mock Get Artist
    mock_artist_detail = DiscogsArtistDetail(id=1, name="Artist")
    mock_discogs_client.get_artist.return_value = mock_artist_detail

    # 4. Mock Scraper
    mock_scraper.fetch_wikipedia_summary.return_value = "Bio text"

    # 5. Mock Enricher
    mock_enrichment_data = EnrichmentData(
        genre="Rock", subgenres=["Industrial"], album_artist_canonical="Artist Canonical"
    )
    mock_enricher.enrich_album.return_value = mock_enrichment_data

    # 6. Mock Track Repo
    mock_db_track = TrackRecord(
        id=10, album_id=1, file_path="/p/1.mp3", filename="1.mp3", track_number=1
    )
    mock_track_repo.get_by_album.return_value = [mock_db_track]

    # Execute
    pipeline.enrich_album(album_record)

    # Verify
    mock_album_repo.mark_found.assert_called_once_with(1, 100, "http://discogs/100")
    mock_track_repo.upsert.assert_called_once()
    saved_track = mock_track_repo.upsert.call_args[0][0]
    assert saved_track.title == "Track 1"
    assert saved_track.genre == "Rock"
    assert "Subgenre:Industrial" in saved_track.grouping


def test_enrich_album_no_discogs_match(
    pipeline: EnrichmentPipeline,
    mock_album_repo: MagicMock,
) -> None:
    album_record = AlbumRecord(
        id=1, artist_guess="Artist", album_guess="Album", folder_path="/path"
    )
    pipeline._selector.find_best_release = MagicMock(return_value=None)

    pipeline.enrich_album(album_record)

    assert not pipeline._discogs_client.get_release.called
    mock_album_repo.mark_not_found.assert_called_once_with(1)


def test_enrich_album_no_discogs_match_adds_manual_review(
    mock_album_repo: MagicMock,
    mock_track_repo: MagicMock,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
) -> None:
    """When no Discogs match, a manual review entry is created if repo is injected."""
    mock_manual_repo = MagicMock()
    pipeline = EnrichmentPipeline(
        album_repo=mock_album_repo,
        track_repo=mock_track_repo,
        discogs_client=mock_discogs_client,
        scraper=mock_scraper,
        enricher=mock_enricher,
        manual_review_repo=mock_manual_repo,
    )
    pipeline._selector.find_best_release = MagicMock(return_value=None)

    album_record = AlbumRecord(
        id=5, artist_guess="Artist", album_guess="Album", folder_path="/path"
    )
    pipeline.enrich_album(album_record)

    mock_album_repo.mark_not_found.assert_called_once_with(5)
    mock_manual_repo.add.assert_called_once_with(5, "No Discogs match")


def test_enrich_album_no_discogs_match_no_manual_repo_is_safe(
    pipeline: EnrichmentPipeline,
    mock_album_repo: MagicMock,
) -> None:
    """When no manual_review_repo is injected, no error is raised."""
    pipeline._selector.find_best_release = MagicMock(return_value=None)
    album_record = AlbumRecord(
        id=1, artist_guess="Artist", album_guess="Album", folder_path="/path"
    )

    pipeline.enrich_album(album_record)  # should not raise

    mock_album_repo.mark_not_found.assert_called_once_with(1)


def _setup_full_pipeline_mocks(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    artist_name: str = "Artist",
) -> None:
    """Shared mock setup for pipeline tests that run to completion.

    Pass artist_name to ensure the release artist matches the album_record.artist_guess
    used in the test (required to pass the artist-name similarity guard).
    """
    mock_search_result = DiscogsSearchResult(
        id=100, type="release", title=f"{artist_name} - Album", resource_url="http://api/100"
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search_result)

    mock_release = DiscogsRelease(
        id=100,
        title="Album",
        artists=[DiscogsArtist(id=1, name=artist_name)],
        tracklist=[DiscogsTrack(position="1", title="Track 1")],
        uri="http://discogs/100",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name=artist_name)
    mock_scraper.fetch_wikipedia_summary.return_value = "Bio text"
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Electronic", subgenres=[], album_artist_canonical=artist_name
    )
    mock_track_repo.get_by_album.return_value = []


def test_enrich_album_without_mb_client_does_not_call_mb(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """When no MB client is injected, mb_links is None in enricher call."""
    _setup_full_pipeline_mocks(
        pipeline, mock_discogs_client, mock_scraper, mock_enricher, mock_track_repo
    )
    album_record = AlbumRecord(
        id=1, artist_guess="Artist", album_guess="Album", folder_path="/path"
    )

    pipeline.enrich_album(album_record)

    call_kwargs = mock_enricher.enrich_album.call_args.kwargs
    assert call_kwargs.get("mb_links") is None


def test_enrich_album_with_mb_client_passes_links_to_enricher(
    pipeline_with_mb: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
    mock_mb_client: MagicMock,
) -> None:
    """When MB client is injected and returns links, they reach the enricher."""
    _setup_full_pipeline_mocks(
        pipeline_with_mb,
        mock_discogs_client,
        mock_scraper,
        mock_enricher,
        mock_track_repo,
        artist_name="Jeff Mills",
    )
    mock_mb_client.find_links.return_value = ["Underground Resistance"]

    album_record = AlbumRecord(
        id=1, artist_guess="Jeff Mills", album_guess="Waveform Transmission", folder_path="/path"
    )

    pipeline_with_mb.enrich_album(album_record)

    mock_mb_client.find_links.assert_called_once_with("Jeff Mills")
    call_kwargs = mock_enricher.enrich_album.call_args.kwargs
    assert call_kwargs.get("mb_links") == ["Underground Resistance"]


def test_enrich_album_matches_track_by_filename_prefix_when_no_track_number(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """Tracks with no TRCK tag (track_number=None) are matched by leading number in filename."""
    mock_search_result = DiscogsSearchResult(
        id=100, type="release", title="Artist - Album", resource_url="http://api/100"
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search_result)

    mock_release = DiscogsRelease(
        id=100,
        title="Album",
        artists=[DiscogsArtist(id=1, name="Artist")],
        tracklist=[
            DiscogsTrack(position="1", title="First Track"),
            DiscogsTrack(position="2", title="Second Track"),
        ],
        uri="http://discogs/100",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name="Artist")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Rock", subgenres=[], album_artist_canonical="Artist"
    )

    # Tracks have no track_number but filenames start with the position
    mock_track_repo.get_by_album.return_value = [
        TrackRecord(
            id=1,
            album_id=1,
            file_path="/p/01 First Track.mp3",
            filename="01 First Track.mp3",
            track_number=None,
        ),
        TrackRecord(
            id=2,
            album_id=1,
            file_path="/p/02 Second Track.mp3",
            filename="02 Second Track.mp3",
            track_number=None,
        ),
    ]

    album_record = AlbumRecord(
        id=1, artist_guess="Artist", album_guess="Album", folder_path="/path"
    )
    pipeline.enrich_album(album_record)

    assert mock_track_repo.upsert.call_count == 2
    saved_titles = {call[0][0].title for call in mock_track_repo.upsert.call_args_list}
    assert saved_titles == {"First Track", "Second Track"}


def test_enrich_album_uses_positional_fallback_when_no_number_available(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """Tracks with no track_number and no numeric filename prefix are matched positionally."""
    mock_search_result = DiscogsSearchResult(
        id=100, type="release", title="Artist - Album", resource_url="http://api/100"
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search_result)

    mock_release = DiscogsRelease(
        id=100,
        title="Album",
        artists=[DiscogsArtist(id=1, name="Artist")],
        tracklist=[
            DiscogsTrack(position="A1", title="Side A Track 1"),
            DiscogsTrack(position="A2", title="Side A Track 2"),
        ],
        uri="http://discogs/100",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name="Artist")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Rock", subgenres=[], album_artist_canonical="Artist"
    )

    # Tracks have neither track_number nor numeric filename prefix
    mock_track_repo.get_by_album.return_value = [
        TrackRecord(
            id=1,
            album_id=1,
            file_path="/p/alpha.mp3",
            filename="alpha.mp3",
            track_number=None,
        ),
        TrackRecord(
            id=2,
            album_id=1,
            file_path="/p/beta.mp3",
            filename="beta.mp3",
            track_number=None,
        ),
    ]

    album_record = AlbumRecord(
        id=1, artist_guess="Artist", album_guess="Album", folder_path="/path"
    )
    pipeline.enrich_album(album_record)

    assert mock_track_repo.upsert.call_count == 2
    saved_titles = {call[0][0].title for call in mock_track_repo.upsert.call_args_list}
    assert saved_titles == {"Side A Track 1", "Side A Track 2"}


def test_enrich_album_fuzzy_matches_existing_title(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """Tracks with no track number or numeric filename are matched to the Discogs
    tracklist by fuzzy-comparing their existing_title against Discogs track titles."""
    mock_search = DiscogsSearchResult(
        id=100,
        master_id=None,
        type="release",
        title="Artist - Album",
        resource_url="http://api/100",
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search)

    mock_release = DiscogsRelease(
        id=100,
        title="Album",
        artists=[DiscogsArtist(id=1, name="Artist")],
        tracklist=[
            DiscogsTrack(position="1", title="Hungry Like the Wolf"),
            DiscogsTrack(position="2", title="Rio"),
            DiscogsTrack(position="3", title="Girls on Film"),
        ],
        uri="http://discogs/100",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name="Artist")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Electronic", subgenres=[], album_artist_canonical="Artist"
    )

    # Filenames have no track numbers; existing_title is slightly different capitalisation
    mock_track_repo.get_by_album.return_value = [
        TrackRecord(
            id=1,
            album_id=1,
            file_path="/p/a.mp3",
            filename="a.mp3",
            track_number=None,
            existing_title="Hungry Like The Wolf",
        ),
        TrackRecord(
            id=2,
            album_id=1,
            file_path="/p/b.mp3",
            filename="b.mp3",
            track_number=None,
            existing_title="Girls On Film",
        ),
        TrackRecord(
            id=3,
            album_id=1,
            file_path="/p/c.mp3",
            filename="c.mp3",
            track_number=None,
            existing_title="Rio",
        ),
    ]

    pipeline.enrich_album(
        AlbumRecord(id=1, artist_guess="Artist", album_guess="Album", folder_path="/path")
    )

    assert mock_track_repo.upsert.call_count == 3
    saved = {c[0][0].existing_title: c[0][0].title for c in mock_track_repo.upsert.call_args_list}
    assert saved["Hungry Like The Wolf"] == "Hungry Like The Wolf"
    assert saved["Girls On Film"] == "Girls On Film"
    assert saved["Rio"] == "Rio"


def test_enrich_album_fuzzy_match_not_used_when_score_too_low(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """Tracks whose existing_title has no good fuzzy match fall back to positional order."""
    mock_search = DiscogsSearchResult(
        id=100,
        master_id=None,
        type="release",
        title="Artist - Album",
        resource_url="http://api/100",
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search)

    mock_release = DiscogsRelease(
        id=100,
        title="Album",
        artists=[DiscogsArtist(id=1, name="Artist")],
        tracklist=[DiscogsTrack(position="1", title="Completely Different Song")],
        uri="http://discogs/100",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name="Artist")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Electronic", subgenres=[], album_artist_canonical="Artist"
    )

    mock_track_repo.get_by_album.return_value = [
        TrackRecord(
            id=1,
            album_id=1,
            file_path="/p/a.mp3",
            filename="a.mp3",
            track_number=None,
            existing_title="ZZZZZ Nothing Like This",
        ),
    ]

    pipeline.enrich_album(
        AlbumRecord(id=1, artist_guess="Artist", album_guess="Album", folder_path="/path")
    )

    # Falls back to positional — the one Discogs track is assigned to the one file
    assert mock_track_repo.upsert.call_count == 1
    assert mock_track_repo.upsert.call_args[0][0].title == "Completely Different Song"


def _make_release(
    track_count: int,
    release_id: int = 100,
    with_headings: bool = False,
) -> DiscogsRelease:
    """Build a DiscogsRelease with track_count real tracks (and optional heading entries)."""
    tracklist: list[DiscogsTrack] = []
    if with_headings:
        tracklist.append(DiscogsTrack(position="", title="Disc 1"))
    for i in range(1, track_count + 1):
        tracklist.append(DiscogsTrack(position=str(i), title=f"Track {i}"))
    return DiscogsRelease(
        id=release_id,
        title="Album",
        artists=[DiscogsArtist(id=1, name="Artist")],
        tracklist=tracklist,
        uri=f"http://discogs/{release_id}",
    )


def _db_tracks(count: int) -> list[TrackRecord]:
    return [
        TrackRecord(
            id=i,
            album_id=1,
            file_path=f"/p/{i:02d} Track {i}.mp3",
            filename=f"{i:02d} Track {i}.mp3",
            track_number=None,
        )
        for i in range(1, count + 1)
    ]


def test_enrich_album_passes_track_count_hint_to_selector(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """Pipeline passes the local file count to find_best_release so the selector can
    pick the version whose track count matches (e.g. 19-track CD over a 7-track single)."""
    mock_search = DiscogsSearchResult(
        id=42,
        master_id=999,
        type="release",
        title="Artist - Album",
        resource_url="http://api/42",
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search)

    right_release = DiscogsRelease(
        id=42,
        title="Album",
        artists=[DiscogsArtist(id=1, name="Artist")],
        tracklist=[
            DiscogsTrack(position="1", title="Right Track 1"),
            DiscogsTrack(position="2", title="Right Track 2"),
            DiscogsTrack(position="3", title="Right Track 3"),
        ],
        uri="http://discogs/42",
    )
    mock_discogs_client.get_release.return_value = right_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name="Artist")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Rock", subgenres=[], album_artist_canonical="Artist"
    )
    mock_track_repo.get_by_album.return_value = _db_tracks(3)

    pipeline.enrich_album(
        AlbumRecord(id=1, artist_guess="Artist", album_guess="Album", folder_path="/path")
    )

    # Selector must have been called with track_count=3
    pipeline._selector.find_best_release.assert_called_once_with("Artist", "Album", track_count=3)
    assert mock_track_repo.upsert.call_count == 3
    saved_titles = {c[0][0].title for c in mock_track_repo.upsert.call_args_list}
    assert saved_titles == {"Right Track 1", "Right Track 2", "Right Track 3"}


def test_enrich_album_adds_manual_review_when_no_version_matches_track_count(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """When no master version matches the file count, the album goes to manual review."""
    mock_manual_repo = MagicMock()
    pipeline._manual_review_repo = mock_manual_repo

    mock_search = DiscogsSearchResult(
        id=7,
        master_id=999,
        type="release",
        title="Artist - Album",
        resource_url="http://api/7",
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search)

    # Selector returns a 7-track release; files have 19 → persistent mismatch
    mock_discogs_client.get_release.return_value = _make_release(track_count=7, release_id=7)
    mock_track_repo.get_by_album.return_value = _db_tracks(19)

    pipeline.enrich_album(
        AlbumRecord(id=1, artist_guess="Artist", album_guess="Album", folder_path="/path")
    )

    mock_album_repo.mark_not_found.assert_called_once_with(1)
    mock_manual_repo.add.assert_called_once()
    assert "19" in mock_manual_repo.add.call_args[0][1]
    mock_track_repo.upsert.assert_not_called()


def test_enrich_album_heading_entries_excluded_from_track_matching(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """Heading entries (empty position) in the Discogs tracklist are ignored."""
    mock_search = DiscogsSearchResult(
        id=100,
        master_id=None,
        type="release",
        title="Artist - Album",
        resource_url="http://api/100",
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search)

    release_with_headings = _make_release(track_count=2, release_id=100, with_headings=True)
    mock_discogs_client.get_release.return_value = release_with_headings
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name="Artist")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Rock", subgenres=[], album_artist_canonical="Artist"
    )
    mock_track_repo.get_by_album.return_value = _db_tracks(2)

    pipeline.enrich_album(
        AlbumRecord(id=1, artist_guess="Artist", album_guess="Album", folder_path="/path")
    )

    # Both real tracks matched; heading not counted
    assert mock_track_repo.upsert.call_count == 2
    saved_titles = {c[0][0].title for c in mock_track_repo.upsert.call_args_list}
    assert saved_titles == {"Track 1", "Track 2"}


def test_enrich_album_mb_empty_result_passes_none(
    pipeline_with_mb: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
    mock_mb_client: MagicMock,
) -> None:
    """When MB returns an empty list, None is passed so the enricher falls back."""
    _setup_full_pipeline_mocks(
        pipeline_with_mb, mock_discogs_client, mock_scraper, mock_enricher, mock_track_repo
    )
    mock_mb_client.find_links.return_value = []

    album_record = AlbumRecord(
        id=1, artist_guess="Artist", album_guess="Album", folder_path="/path"
    )

    pipeline_with_mb.enrich_album(album_record)

    call_kwargs = mock_enricher.enrich_album.call_args.kwargs
    assert call_kwargs.get("mb_links") is None


def test_enrich_album_uses_track_artist_for_compilation(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """On a Various Artists compilation each track gets its per-track Discogs artist,
    not the album-level 'Various' artist."""
    mock_search = DiscogsSearchResult(
        id=100, type="release", title="Various - Compilation", resource_url="http://api/100"
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search)

    mock_release = DiscogsRelease(
        id=100,
        title="Compilation",
        artists=[DiscogsArtist(id=1, name="Various")],
        tracklist=[
            DiscogsTrack(
                position="1",
                title="Track One",
                artists=[DiscogsTrackArtist(id=10, name="Artist Alpha")],
            ),
            DiscogsTrack(
                position="2",
                title="Track Two",
                artists=[DiscogsTrackArtist(id=20, name="Artist Beta")],
            ),
        ],
        uri="http://discogs/100",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name="Various")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Electronic", subgenres=[], album_artist_canonical="Various"
    )
    mock_track_repo.get_by_album.return_value = [
        TrackRecord(id=1, album_id=1, file_path="/p/1.mp3", filename="1.mp3", track_number=1),
        TrackRecord(id=2, album_id=1, file_path="/p/2.mp3", filename="2.mp3", track_number=2),
    ]

    pipeline.enrich_album(
        AlbumRecord(id=1, artist_guess="Various", album_guess="Compilation", folder_path="/path")
    )

    assert mock_track_repo.upsert.call_count == 2
    saved = {c[0][0].title: c[0][0] for c in mock_track_repo.upsert.call_args_list}
    assert saved["Track One"].artist == "Artist Alpha"
    assert saved["Track Two"].artist == "Artist Beta"
    # Compilation flag set, album_artist cleared (TCMP used instead of TPE2)
    assert saved["Track One"].compilation is True
    assert saved["Track One"].album_artist is None
    assert saved["Track Two"].compilation is True
    assert saved["Track Two"].album_artist is None


def test_enrich_album_falls_back_to_album_artist_when_no_track_artist(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """When a track has no per-track artists, the album-level artist is used."""
    mock_search = DiscogsSearchResult(
        id=100, type="release", title="Artist - Album", resource_url="http://api/100"
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search)

    mock_release = DiscogsRelease(
        id=100,
        title="Album",
        artists=[DiscogsArtist(id=1, name="Artist")],
        tracklist=[DiscogsTrack(position="1", title="Solo Track")],  # no track artists
        uri="http://discogs/100",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name="Artist")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Rock", subgenres=[], album_artist_canonical="Artist"
    )
    mock_track_repo.get_by_album.return_value = [
        TrackRecord(id=1, album_id=1, file_path="/p/1.mp3", filename="1.mp3", track_number=1),
    ]

    pipeline.enrich_album(
        AlbumRecord(id=1, artist_guess="Artist", album_guess="Album", folder_path="/path")
    )

    saved = mock_track_repo.upsert.call_args[0][0]
    assert saved.artist == "Artist"


def test_enrich_album_uses_discogs_release_artist_not_mangled_folder_name(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """When a folder name is mangled by the OS (e.g. 'E.C.M_' for 'E.C.M.'), the Discogs
    release artist name should be written to the track, not the mangled folder artist_guess."""
    mock_search = DiscogsSearchResult(
        id=100, type="release", title="E.C.M. - Blechreiz", resource_url="http://api/100"
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search)

    mock_release = DiscogsRelease(
        id=100,
        title="Blechreiz",
        artists=[DiscogsArtist(id=1, name="E.C.M.")],
        tracklist=[DiscogsTrack(position="1", title="Gift")],  # no per-track artists
        uri="http://discogs/100",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name="E.C.M.")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Rock", subgenres=[], album_artist_canonical="E.C.M."
    )
    mock_track_repo.get_by_album.return_value = [
        TrackRecord(id=1, album_id=1, file_path="/p/1.mp3", filename="1.mp3", track_number=1),
    ]

    pipeline.enrich_album(
        AlbumRecord(id=1, artist_guess="E.C.M_", album_guess="Blechreiz", folder_path="/path")
    )

    saved = mock_track_repo.upsert.call_args[0][0]
    # Must be the Discogs canonical name, not the OS-mangled folder artist_guess
    assert saved.artist == "E.C.M."


def test_enrich_album_strips_discogs_disambiguation_from_track_artist(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """Discogs disambiguation suffixes like '(2)' are stripped from track artist names."""
    mock_search = DiscogsSearchResult(
        id=100, type="release", title="Various - Compilation", resource_url="http://api/100"
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search)

    mock_release = DiscogsRelease(
        id=100,
        title="Compilation",
        artists=[DiscogsArtist(id=1, name="Various")],
        tracklist=[
            DiscogsTrack(
                position="1",
                title="Song",
                artists=[DiscogsTrackArtist(id=5, name="Dave Clarke (2)")],
            )
        ],
        uri="http://discogs/100",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name="Various")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Electronic", subgenres=[], album_artist_canonical="Various"
    )
    mock_track_repo.get_by_album.return_value = [
        TrackRecord(id=1, album_id=1, file_path="/p/1.mp3", filename="1.mp3", track_number=1),
    ]

    pipeline.enrich_album(
        AlbumRecord(id=1, artist_guess="Various", album_guess="Compilation", folder_path="/path")
    )

    saved = mock_track_repo.upsert.call_args[0][0]
    assert saved.artist == "Dave Clarke"


def test_enrich_album_sets_compilation_and_clears_album_artist_for_various(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """When the album artist resolves to 'Various', compilation=True and album_artist=None."""
    mock_search = DiscogsSearchResult(
        id=100, type="release", title="Various - Compilation", resource_url="http://api/100"
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search)

    mock_release = DiscogsRelease(
        id=100,
        title="Compilation",
        artists=[DiscogsArtist(id=1, name="Various")],
        tracklist=[DiscogsTrack(position="1", title="Track One")],
        uri="http://discogs/100",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name="Various")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Electronic", subgenres=[], album_artist_canonical="Various"
    )
    mock_track_repo.get_by_album.return_value = [
        TrackRecord(id=1, album_id=1, file_path="/p/1.mp3", filename="1.mp3", track_number=1),
    ]

    pipeline.enrich_album(
        AlbumRecord(id=1, artist_guess="Various", album_guess="Compilation", folder_path="/path")
    )

    saved = mock_track_repo.upsert.call_args[0][0]
    assert saved.compilation is True
    assert saved.album_artist is None


def test_enrich_album_from_release_id_bypasses_selector(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """enrich_album_from_release_id fetches the release directly and never calls the selector."""
    mock_release = DiscogsRelease(
        id=213255,
        title="Greatest",
        artists=[DiscogsArtist(id=1, name="Duran Duran")],
        tracklist=[
            DiscogsTrack(position="1", title="Is There Something I Should Know?"),
            DiscogsTrack(position="2", title="The Reflex"),
        ],
        uri="http://discogs/213255",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name="Duran Duran")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Pop", subgenres=[], album_artist_canonical="Duran Duran"
    )
    mock_track_repo.get_by_album.return_value = [
        TrackRecord(id=1, album_id=1, file_path="/p/1.mp3", filename="1.mp3", track_number=1),
        TrackRecord(id=2, album_id=1, file_path="/p/2.mp3", filename="2.mp3", track_number=2),
    ]

    album_record = AlbumRecord(
        id=1, artist_guess="Duran Duran", album_guess="Greatest", folder_path="/path"
    )
    pipeline.enrich_album_from_release_id(album_record, 213255)

    # Selector must NOT have been called — search/master-versions never touched
    assert not mock_discogs_client.search.called
    # Release fetched directly
    mock_discogs_client.get_release.assert_called_once_with(213255)
    # Both tracks enriched
    assert mock_track_repo.upsert.call_count == 2
    saved_titles = {c[0][0].title for c in mock_track_repo.upsert.call_args_list}
    assert saved_titles == {"Is There Something I Should Know?", "The Reflex"}


def test_enrich_album_404_on_get_release_marks_not_found(
    pipeline: EnrichmentPipeline,
    mock_album_repo: MagicMock,
    mock_track_repo: MagicMock,
    mock_discogs_client: MagicMock,
) -> None:
    """A 404 from get_release is handled gracefully: album marked not_found, no crash."""
    import httpx

    album_record = AlbumRecord(
        id=1, artist_guess="Black Sabbath", album_guess="Paranoid", folder_path="/path"
    )
    mock_search_result = DiscogsSearchResult(
        id=9061527,
        type="release",
        title="Black Sabbath - Paranoid",
        resource_url="http://api/9061527",
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search_result)
    mock_track_repo.get_by_album.return_value = [
        TrackRecord(id=1, album_id=1, file_path="/p/1.mp3", filename="1.mp3")
    ]

    request = httpx.Request("GET", "https://api.discogs.com/releases/9061527")
    response = httpx.Response(404, request=request)
    mock_discogs_client.get_release.side_effect = httpx.HTTPStatusError(
        "404 Not Found", request=request, response=response
    )

    pipeline.enrich_album(album_record)

    mock_album_repo.mark_not_found.assert_called_once_with(1)
    mock_track_repo.upsert.assert_not_called()


def test_enrich_with_release_passes_release_artist_to_enricher(
    pipeline_with_mb: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_mb_client: MagicMock,
) -> None:
    """_enrich_with_release() passes release.artists[0].name as release_artist to enricher."""
    mock_search_result = DiscogsSearchResult(
        id=200, type="release", title="Scientist - Album", resource_url="http://api/200"
    )
    pipeline_with_mb._selector.find_best_release = MagicMock(return_value=mock_search_result)

    mock_release = DiscogsRelease(
        id=200,
        title="Scientist Rids the World",
        artists=[DiscogsArtist(id=42, name="Scientist")],
        tracklist=[DiscogsTrack(position="1", title="Track")],
        uri="http://discogs/200",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(
        id=42, name="Scientist", realname="Hopeton Overton Brown"
    )
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Reggae", subgenres=[], album_artist_canonical="Scientist"
    )
    mock_track_repo.get_by_album.return_value = []
    mock_mb_client.find_links.return_value = []
    mock_mb_client.find_area.return_value = (None, None)

    album_record = AlbumRecord(
        id=1, artist_guess="Scientist", album_guess="Scientist Rids the World", folder_path="/p"
    )
    pipeline_with_mb.enrich_album(album_record)

    call_kwargs = mock_enricher.enrich_album.call_args.kwargs
    assert call_kwargs.get("release_artist") == "Scientist"


def test_enrich_with_release_passes_mb_origin_to_enricher(
    pipeline_with_mb: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_mb_client: MagicMock,
) -> None:
    """_enrich_with_release() calls find_area() and passes mb_origin to enricher."""
    _setup_full_pipeline_mocks(
        pipeline_with_mb, mock_discogs_client, mock_scraper, mock_enricher, mock_track_repo
    )
    mock_mb_client.find_links.return_value = []
    mock_mb_client.find_area.return_value = ("Cullen", "Scotland")

    album_record = AlbumRecord(
        id=1, artist_guess="Artist", album_guess="Album", folder_path="/path"
    )
    pipeline_with_mb.enrich_album(album_record)

    mock_mb_client.find_area.assert_called_once_with("Artist")
    call_kwargs = mock_enricher.enrich_album.call_args.kwargs
    assert call_kwargs.get("mb_origin") == ("Cullen", "Scotland")


# ---------------------------------------------------------------------------
# Multi-disc filename matching (Strategy 0)
# ---------------------------------------------------------------------------


def test_enrich_album_matches_multidisc_track_by_disc_filename_prefix(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """Filenames like '2-01 Song.mp3' map to Discogs position '2-1' (disc 2, track 1)."""
    mock_search_result = DiscogsSearchResult(
        id=200, type="release", title="Artist - Multi", resource_url="http://api/200"
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search_result)

    mock_release = DiscogsRelease(
        id=200,
        title="Multi",
        artists=[DiscogsArtist(id=1, name="Artist")],
        tracklist=[
            DiscogsTrack(position="1-1", title="Disc One Track One"),
            DiscogsTrack(position="1-2", title="Disc One Track Two"),
            DiscogsTrack(position="2-1", title="Disc Two Track One"),
            DiscogsTrack(position="2-2", title="Disc Two Track Two"),
        ],
        uri="http://discogs/200",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name="Artist")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Rock", subgenres=[], album_artist_canonical="Artist"
    )

    mock_track_repo.get_by_album.return_value = [
        TrackRecord(
            id=1,
            album_id=1,
            file_path="/p/1-01 D1T1.mp3",
            filename="1-01 D1T1.mp3",
            track_number=None,
        ),
        TrackRecord(
            id=2,
            album_id=1,
            file_path="/p/1-02 D1T2.mp3",
            filename="1-02 D1T2.mp3",
            track_number=None,
        ),
        TrackRecord(
            id=3,
            album_id=1,
            file_path="/p/2-01 D2T1.mp3",
            filename="2-01 D2T1.mp3",
            track_number=None,
        ),
        TrackRecord(
            id=4,
            album_id=1,
            file_path="/p/2-02 D2T2.mp3",
            filename="2-02 D2T2.mp3",
            track_number=None,
        ),
    ]

    album_record = AlbumRecord(
        id=1, artist_guess="Artist", album_guess="Multi", folder_path="/path"
    )
    pipeline.enrich_album(album_record)

    assert mock_track_repo.upsert.call_count == 4
    saved = {c[0][0].filename: c[0][0].title for c in mock_track_repo.upsert.call_args_list}
    assert saved["1-01 D1T1.mp3"] == "Disc One Track One"
    assert saved["2-01 D2T1.mp3"] == "Disc Two Track One"
    assert saved["2-02 D2T2.mp3"] == "Disc Two Track Two"


def test_enrich_album_multidisc_sets_disc_number_on_track(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """disc_number is extracted from the Discogs position (e.g. '2-1' → disc_number=2)."""
    mock_search_result = DiscogsSearchResult(
        id=201, type="release", title="Artist - Multi2", resource_url="http://api/201"
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search_result)

    mock_release = DiscogsRelease(
        id=201,
        title="Multi2",
        artists=[DiscogsArtist(id=1, name="Artist")],
        tracklist=[
            DiscogsTrack(position="1-1", title="Disc One"),
            DiscogsTrack(position="2-1", title="Disc Two"),
        ],
        uri="http://discogs/201",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name="Artist")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Rock", subgenres=[], album_artist_canonical="Artist"
    )

    mock_track_repo.get_by_album.return_value = [
        TrackRecord(
            id=1,
            album_id=1,
            file_path="/p/1-01 Disc One.mp3",
            filename="1-01 Disc One.mp3",
            track_number=None,
        ),
        TrackRecord(
            id=2,
            album_id=1,
            file_path="/p/2-01 Disc Two.mp3",
            filename="2-01 Disc Two.mp3",
            track_number=None,
        ),
    ]

    album_record = AlbumRecord(id=1, artist_guess="Artist", album_guess="Multi2", folder_path="/p")
    pipeline.enrich_album(album_record)

    saved = {c[0][0].filename: c[0][0] for c in mock_track_repo.upsert.call_args_list}
    assert saved["1-01 Disc One.mp3"].disc_number == 1
    assert saved["2-01 Disc Two.mp3"].disc_number == 2


# ---------------------------------------------------------------------------
# Artist name similarity guard
# ---------------------------------------------------------------------------


def test_enrich_album_rejects_artist_name_mismatch(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """When release artist is clearly wrong (e.g. 'Astrud Gilberto' in a 'Cat Stevens' folder),
    the pipeline rejects the match and adds a manual review entry."""
    mock_manual_repo = MagicMock()
    test_pipeline = EnrichmentPipeline(
        album_repo=mock_album_repo,
        track_repo=mock_track_repo,
        discogs_client=mock_discogs_client,
        scraper=mock_scraper,
        enricher=mock_enricher,
        manual_review_repo=mock_manual_repo,
    )

    mock_search_result = DiscogsSearchResult(
        id=300, type="release", title="Astrud Gilberto - Something", resource_url="http://api/300"
    )
    test_pipeline._selector.find_best_release = MagicMock(return_value=mock_search_result)

    mock_release = DiscogsRelease(
        id=300,
        title="Something",
        artists=[DiscogsArtist(id=5, name="Astrud Gilberto")],
        tracklist=[DiscogsTrack(position="1", title="Track 1")],
        uri="http://discogs/300",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_track_repo.get_by_album.return_value = [
        TrackRecord(id=1, album_id=1, file_path="/p/t.mp3", filename="t.mp3")
    ]

    album_record = AlbumRecord(
        id=1, artist_guess="Cat Stevens", album_guess="Tea for the Tillerman", folder_path="/path"
    )
    test_pipeline.enrich_album(album_record)

    # Enricher should NOT be called — the match was rejected
    mock_enricher.enrich_album.assert_not_called()
    mock_album_repo.mark_not_found.assert_called_once_with(1)
    mock_manual_repo.add.assert_called_once()
    reason = mock_manual_repo.add.call_args[0][1]
    assert "mismatch" in reason.lower()


def test_enrich_album_various_artist_skips_name_validation(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """Various Artists compilations bypass the artist name similarity check."""
    mock_search_result = DiscogsSearchResult(
        id=400, type="release", title="Various - Compilation", resource_url="http://api/400"
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search_result)

    mock_release = DiscogsRelease(
        id=400,
        title="Compilation",
        artists=[DiscogsArtist(id=99, name="Various Artists")],
        tracklist=[DiscogsTrack(position="1", title="Track 1")],
        uri="http://discogs/400",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=99, name="Various Artists")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Electronic", subgenres=[], album_artist_canonical="various artists"
    )
    mock_track_repo.get_by_album.return_value = [
        TrackRecord(id=1, album_id=1, file_path="/p/t.mp3", filename="t.mp3", track_number=1)
    ]

    album_record = AlbumRecord(
        id=1, artist_guess="Compilations", album_guess="Compilation", folder_path="/path"
    )
    # Should not raise or reject — Various Artists always passes
    pipeline.enrich_album(album_record)

    mock_enricher.enrich_album.assert_called_once()


# ---------------------------------------------------------------------------
# Non-numeric (vinyl / roman-numeral) track positions
# ---------------------------------------------------------------------------


def test_enrich_album_vinyl_positions_generate_numeric_track_numbers(
    pipeline: EnrichmentPipeline,
    mock_discogs_client: MagicMock,
    mock_scraper: MagicMock,
    mock_enricher: MagicMock,
    mock_track_repo: MagicMock,
    mock_album_repo: MagicMock,
) -> None:
    """Vinyl Discogs positions (A1, A2, B1, B2) are converted to sequential numeric TRCK values."""
    mock_search_result = DiscogsSearchResult(
        id=500, type="release", title="Artist - Vinyl Album", resource_url="http://api/500"
    )
    pipeline._selector.find_best_release = MagicMock(return_value=mock_search_result)

    mock_release = DiscogsRelease(
        id=500,
        title="Vinyl Album",
        artists=[DiscogsArtist(id=1, name="Artist")],
        tracklist=[
            DiscogsTrack(position="A1", title="Side A Track One"),
            DiscogsTrack(position="A2", title="Side A Track Two"),
            DiscogsTrack(position="B1", title="Side B Track One"),
            DiscogsTrack(position="B2", title="Side B Track Two"),
        ],
        uri="http://discogs/500",
    )
    mock_discogs_client.get_release.return_value = mock_release
    mock_discogs_client.get_artist.return_value = DiscogsArtistDetail(id=1, name="Artist")
    mock_scraper.fetch_wikipedia_summary.return_value = ""
    mock_enricher.enrich_album.return_value = EnrichmentData(
        genre="Rock", subgenres=[], album_artist_canonical="Artist"
    )

    # Files named conventionally: "01 Side A Track One.mp3" etc.
    mock_track_repo.get_by_album.return_value = [
        TrackRecord(
            id=1,
            album_id=1,
            file_path="/p/01 Side A Track One.mp3",
            filename="01 Side A Track One.mp3",
            track_number=None,
            existing_title="Side A Track One",
        ),
        TrackRecord(
            id=2,
            album_id=1,
            file_path="/p/02 Side A Track Two.mp3",
            filename="02 Side A Track Two.mp3",
            track_number=None,
            existing_title="Side A Track Two",
        ),
        TrackRecord(
            id=3,
            album_id=1,
            file_path="/p/03 Side B Track One.mp3",
            filename="03 Side B Track One.mp3",
            track_number=None,
            existing_title="Side B Track One",
        ),
        TrackRecord(
            id=4,
            album_id=1,
            file_path="/p/04 Side B Track Two.mp3",
            filename="04 Side B Track Two.mp3",
            track_number=None,
            existing_title="Side B Track Two",
        ),
    ]

    album_record = AlbumRecord(
        id=1, artist_guess="Artist", album_guess="Vinyl Album", folder_path="/path"
    )
    pipeline.enrich_album(album_record)

    assert mock_track_repo.upsert.call_count == 4
    saved = {c[0][0].title: c[0][0] for c in mock_track_repo.upsert.call_args_list}

    # track_num should be numeric: A1→"01/04", A2→"02/04", B1→"03/04", B2→"04/04"
    assert saved["Side A Track One"].track_num == "01/04"
    assert saved["Side A Track Two"].track_num == "02/04"
    assert saved["Side B Track One"].track_num == "03/04"
    assert saved["Side B Track Two"].track_num == "04/04"
    # Vinyl tracks have no disc number
    assert saved["Side A Track One"].disc_number is None
