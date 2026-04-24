"""Pydantic models for tag integrity issues."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class IssueKind(StrEnum):
    """Taxonomy of tag integrity issues detected by IntegrityScanner."""

    ALBUM_ARTIST_MISMATCH = "album_artist_mismatch"
    ALBUM_MISMATCH = "album_mismatch"
    INCONSISTENT_ALBUM_ARTIST = "inconsistent_album_artist"
    INCONSISTENT_ALBUM = "inconsistent_album"
    COMPILATION_ARTIST = "compilation_artist"
    ALL_UNTITLED = "all_untitled"
    TRACK_TITLE = "track_title"


class TagIssue(BaseModel):
    """A single tag integrity problem found in an album folder."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    artist_folder: str
    album_folder: str
    folder_path: str | None = None
    issue_kind: IssueKind
    detail: str
    status: str = "pending"
    created_at: str | None = None
    resolved_at: str | None = None
