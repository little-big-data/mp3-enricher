"""Helpers for editing the pipe-separated GRP1/TIT1 grouping string."""

from __future__ import annotations

_PLACEHOLDER = "none"


def ensure_grouping_segment(existing: str | None, key: str, value: str) -> str:
    """Return ``existing`` guaranteeing its ``key`` segment contains ``value``.

    Segments are separated by ``|``; a segment is ``key:v1, v2``. Keys and values
    are compared case-insensitively (values per comma item, never by substring).
    If ``value`` is already present, ``existing`` is returned unchanged. Otherwise
    the value is merged into the existing ``key`` segment (keeping its key casing,
    replacing a ``None`` placeholder) or appended as a new segment.
    """
    raw = existing or ""
    segments = [seg.strip() for seg in raw.split("|") if seg.strip()]
    wanted_key = key.lower()
    wanted_value = value.strip().lower()

    for index, segment in enumerate(segments):
        seg_key, sep, seg_values = segment.partition(":")
        if not sep or seg_key.strip().lower() != wanted_key:
            continue
        items = [item.strip() for item in seg_values.split(",") if item.strip()]
        if wanted_value in (item.lower() for item in items):
            return existing if existing is not None else ""
        items = [item for item in items if item.lower() != _PLACEHOLDER]
        items.append(value.strip())
        segments[index] = f"{seg_key.strip()}:{', '.join(items)}"
        return " | ".join(segments)

    segments.append(f"{key}:{value.strip()}")
    return " | ".join(segments)
