from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TrackOverride(BaseModel):
    position: str
    is_instrumental: bool | None = None
    is_cover: bool | None = None
    is_remix: bool | None = None


class EnrichmentData(BaseModel):
    album_artist_canonical: str | None = None
    origin_city: str | None = None
    origin_country: str | None = None
    gender: Literal["Male", "Female", "Non-binary", "Mixed", "Unknown"] | None = "Unknown"
    race: str | None = None
    label: str | None = None
    link: str | None = None
    holiday: Literal["Halloween", "Christmas", "Thanksgiving", "Easter", "None"] | None = "None"
    genre: str | None = None
    subgenres: list[str] = Field(default_factory=list)
    track_overrides: list[TrackOverride] = Field(default_factory=list)

    def to_grp1(self, track_override: TrackOverride | None = None) -> str:
        """
        Origin:Detroit, US | Gender:Male | Race:Black | Instrumental:No |
        Subgenre:Techno, Detroit Techno | Cover:No | Remix:No |
        link:Underground Resistance | Holiday:None
        """
        origin_parts = [p for p in [self.origin_city, self.origin_country] if p]
        origin = ", ".join(origin_parts) if origin_parts else ""

        subgenres = ", ".join(self.subgenres) if self.subgenres else ""

        is_instrumental = ""
        is_cover = "No"
        is_remix = "No"

        if track_override:
            if track_override.is_instrumental is not None:
                is_instrumental = "Yes" if track_override.is_instrumental else "No"
            if track_override.is_cover is not None:
                is_cover = "Yes" if track_override.is_cover else "No"
            if track_override.is_remix is not None:
                is_remix = "Yes" if track_override.is_remix else "No"

        parts = []
        if origin:
            parts.append(f"Origin:{origin}")
        if self.gender and self.gender != "Unknown":
            parts.append(f"Gender:{self.gender}")
        if self.race and self.race != "Unknown":
            parts.append(f"Race:{self.race}")
        if is_instrumental:
            parts.append(f"Instrumental:{is_instrumental}")
        if subgenres:
            parts.append(f"Subgenre:{subgenres}")
        if is_cover == "Yes":
            parts.append(f"Cover:{is_cover}")
        if is_remix == "Yes":
            parts.append(f"Remix:{is_remix}")
        if self.label:
            parts.append(f"Label:{self.label}")
        if self.link:
            parts.append(f"link:{self.link}")
        if self.holiday and self.holiday != "None":
            parts.append(f"Holiday:{self.holiday}")

        return " | ".join(parts)
