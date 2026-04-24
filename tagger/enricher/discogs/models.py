from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl, field_validator


class DiscogsArtist(BaseModel):
    id: int | None = None
    name: str


class DiscogsImage(BaseModel):
    type: str
    resource_url: HttpUrl


class DiscogsTrackArtist(BaseModel):
    id: int = 0
    name: str
    anv: str = ""
    join: str = ""


class DiscogsTrack(BaseModel):
    position: str
    title: str
    duration: str | None = None
    artists: list[DiscogsTrackArtist] = Field(default_factory=list)


class DiscogsLabel(BaseModel):
    id: int | None = None
    name: str
    catno: str | None = None


class DiscogsRelease(BaseModel):
    id: int
    title: str
    year: int | None = None
    released: str | None = None
    master_id: int | None = None
    master_url: HttpUrl | None = None
    uri: str | None = None
    artists: list[DiscogsArtist] = Field(default_factory=list)
    images: list[DiscogsImage] = Field(default_factory=list)
    tracklist: list[DiscogsTrack] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)
    labels: list[DiscogsLabel] = Field(default_factory=list)


class DiscogsArtistDetail(BaseModel):
    id: int
    name: str
    realname: str | None = None
    profile: str | None = None
    urls: list[str] = Field(default_factory=list)
    namevariations: list[str] = Field(default_factory=list)


class DiscogsSearchResult(BaseModel):
    id: int
    type: str
    master_id: int | None = None
    title: str
    year: int | None = None
    resource_url: HttpUrl
    genre: list[str] = Field(default_factory=list)
    style: list[str] = Field(default_factory=list)
    format: list[str] = Field(default_factory=list)

    @field_validator("year", mode="before")
    @classmethod
    def parse_year(cls, v: str | int | None) -> int | None:
        if v is None:
            return None
        if isinstance(v, str):
            if not v.strip():
                return None
            try:
                # search results sometimes have "1989" or "1989-10-20" or empty
                return int(v.split("-")[0])
            except (ValueError, IndexError):
                return None
        return v
