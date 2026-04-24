"""Prompt construction for link (affiliation) detection."""

from __future__ import annotations


def build_link_prompt(
    artist: str,
    album: str,
    label: str | None,
    genres: list[str],
    featured_artists: list[str],
) -> str:
    """Build the user-turn prompt asking Claude to identify link affiliations.

    The prompt provides all available context signals and requests a strict JSON
    response so the caller can parse without ambiguity.
    """
    lines: list[str] = [
        f"Artist: {artist}",
        f"Album: {album}",
    ]
    if label:
        lines.append(f"Label: {label}")
    if genres:
        lines.append(f"Genres: {', '.join(genres)}")
    if featured_artists:
        lines.append(f"Featured artists on this album: {', '.join(featured_artists)}")

    context_block = "\n".join(lines)

    featured_guidance = (
        " Featured artists on the album can be a strong signal — if several of them"
        " are known members of the same group as the primary artist, that is good evidence."
        if featured_artists
        else ""
    )

    return f"""\
You are a music metadata expert. Given the following album details, identify any musical \
collectives, supergroups, or influential label families that the primary artist belongs to.

{context_block}

Focus on formal group memberships and well-known creative partnerships (e.g. Wu-Tang Clan \
members, Giegling label roster, Native Tongues collective). Do not include loose stylistic \
similarities.{featured_guidance}

Respond with a JSON object and nothing else:
{{"links": ["Name1", "Name2"]}}

Use an empty array if the artist has no clear affiliations:
{{"links": []}}"""
