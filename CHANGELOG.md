# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-04-24

### Added

- Parallel enrichment pipeline: scans MP3 folders, fetches Discogs / Wikipedia / MusicBrainz
  metadata, and writes enriched ID3 tags via `mutagen`. Controlled by `--workers`.
- Five-strategy track-matching algorithm that handles multi-disc albums, non-numeric Discogs
  positions (vinyl A/B sides, roman numerals), and fuzzy ID3 title matching.
- ID3 tag writing with full metadata: title, artist, album artist, year, genre, grouping
  (pipe-delimited `Origin:City | Gender:... | Subgenre:... | Label:... | link:...`),
  disc number, and embedded album art.
- Manual-review CSV workflow: albums that cannot be matched automatically are exported to a
  CSV; after a human supplies Discogs URLs the `process-manual` command applies them.
- `scan-integrity` command: walks the library and reports ID3 tag / folder-name mismatches
  (album artist mismatch, inconsistent tags, all-untitled albums, etc.).
- `retry-not-found` command: re-runs Discogs enrichment on all `not_found` albums without
  a subprocess round-trip.
- `prefill-master-urls` command: pre-fills the `user_discogs_url` column in a manual-review
  CSV using two search passes, reducing reviewer lookup work.
- `enrich-missing` command: re-scans artist directories for albums referenced in a reviewed
  CSV that are missing from the database.
- Claude and Gemini LLM clients for collective / affiliation detection, written as injectable
  `Protocol` implementations so either backend can be swapped at runtime.
- SQLite repository layer with versioned migrations and WAL-mode concurrency.
- Structured logging via `structlog` (JSON in CI, pretty console in development).
- Pydantic v2 models at every external data boundary (Discogs API, LLM JSON output, DB rows).
- `tenacity`-based retry decorator with server-provided `Retry-After` awareness and
  exponential back-off.
- Token-bucket rate limiter used across all Discogs API calls.
- Windows SMB share fallback: when `mutagen`'s in-place save fails with `EINVAL`, the writer
  copies to a local temp file and moves it back to avoid cross-device shutil issues.
- Four Claude Code skills (`/scan-integrity`, `/retry-not-found`, `/prefill-master-urls`,
  `/enrich-missing`) matching the four operational CLI commands.

### Fixed

- Apostrophe / contraction handling in title-case normalisation so that "She's Gone" is not
  upper-cased to "She'S Gone" (#79).
- Multi-disc track-title shifting: disc-2 files now map directly to Discogs position "N-M"
  rather than falling through to an incorrect positional index.
- Artist-name sanity check rejects Discogs matches where folder artist and release artist have
  < 40% token-set similarity (e.g. Cat Stevens vs. Astrud Gilberto class of false positives).
- Discogs release artist used as track-artist fallback when per-track artist list is empty.

[Unreleased]: https://github.com/jschloman/mp3-enricher/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jschloman/mp3-enricher/releases/tag/v0.1.0
