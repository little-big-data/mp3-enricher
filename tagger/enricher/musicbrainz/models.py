"""Pydantic models for the MusicBrainz API (ws/2) responses."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MusicBrainzArtistRef(BaseModel):
    """Minimal artist reference embedded in relation objects."""

    id: str
    name: str


class MusicBrainzRelation(BaseModel):
    """A single typed relationship on an artist entity."""

    type: str
    direction: str
    artist: MusicBrainzArtistRef


class MusicBrainzArea(BaseModel):
    """Area (country, city, or region) referenced on an artist entity."""

    name: str


class MusicBrainzArtistDetail(BaseModel):
    """Artist entity returned by GET /ws/2/artist/{mbid}?inc=artist-rels."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    area: MusicBrainzArea | None = None
    begin_area: MusicBrainzArea | None = Field(default=None, alias="begin-area")
    relations: list[MusicBrainzRelation] = Field(default_factory=list)


class MusicBrainzSearchArtist(BaseModel):
    """Single artist entry in a search response."""

    id: str
    name: str
    score: int = 0


class MusicBrainzSearchResponse(BaseModel):
    """Response envelope for GET /ws/2/artist?query=…"""

    artists: list[MusicBrainzSearchArtist] = Field(default_factory=list)
