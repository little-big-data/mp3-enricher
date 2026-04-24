from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AlbumRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    folder_path: str
    artist_guess: str | None = None
    album_guess: str | None = None
    discogs_release_id: int | None = None
    discogs_url: str | None = None
    enrichment_status: str = "pending"  # pending | found | not_found | manual | error
    written_status: str = "pending"  # pending | done | error
    notes: str | None = None


class TrackRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    album_id: int
    file_path: str
    filename: str
    track_number: int | None = None
    disc_number: int | None = None
    existing_title: str | None = None
    existing_artist: str | None = None

    # Enriched fields
    title: str | None = None
    artist: str | None = None
    album_artist: str | None = None
    album_title: str | None = None
    year: int | None = None
    track_num: str | None = None  # "N/M" format
    genre: str | None = None
    grouping: str | None = None
    art_path: str | None = None
    compilation: bool = False

    enrichment_status: str = "pending"
    written_status: str = "pending"
    notes: str | None = None
