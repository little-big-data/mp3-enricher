# CLAUDE.md — AI Code Assistant Instructions

> This file configures Claude Code for the `mp3-enricher` project.
> Read this file in full before generating, modifying, or reviewing any code.

---

## Project Mission

`mp3-enricher` is a **production-grade, parallelised, idempotent** CLI pipeline that
enriches ID3 tags on large MP3 libraries using Discogs, Wikipedia, and an LLM
(Claude or Gemini). This file is the authoritative source for engineering
process and functional requirements.

---

## Non-Negotiable Engineering Standards

These rules apply to every file you touch. No exceptions.

### 1. Test-Driven Development (TDD) is Mandatory

Follow the **Red → Green → Refactor** cycle for every feature and bug fix.

1. **Write the test first.** The test must fail before any implementation exists.
2. **Write the minimum code to make the test pass.** No speculative features.
3. **Refactor** — clean up duplication, naming, and structure while keeping tests green.

When asked to implement a feature, **always produce the test file first** in your
response, followed by the implementation. If the user asks for implementation only,
remind them of this rule and ask for confirmation before proceeding.

```
# Correct order in every response that adds a feature:
# 1. tests/test_<module>.py  (new or updated)
# 2. tagger/<module>.py      (new or updated)
# 3. Any fixtures / conftest changes
```

### 2. Type Annotations Everywhere

All function signatures, return types, and class attributes must be fully annotated.
Use `from __future__ import annotations` at the top of every module.

```python
# ✅ correct
def find_best_release(results: list[DiscogsRelease], threshold: int = 85) -> DiscogsRelease | None:
    ...

# ❌ wrong
def find_best_release(results, threshold=85):
    ...
```

Use `TypeAlias` for complex repeated types and `TypedDict` / `dataclass` / `pydantic`
models for structured data — never plain `dict[str, Any]` at function boundaries.

### 3. Pydantic v2 for All Data Models

Every external data boundary (Discogs API response, LLM JSON output, CSV row,
DB result row) must be modelled as a `pydantic.BaseModel`. Validation happens at the
boundary; internal code works with typed model instances.

```python
from pydantic import BaseModel, Field, HttpUrl

class DiscogsRelease(BaseModel):
    id: int
    title: str
    year: int | None = None
    released: str | None = None
    images: list[DiscogsImage] = Field(default_factory=list)
    tracklist: list[DiscogsTrack] = Field(default_factory=list)
```

### 4. Dependency Injection over Global State

Never import singletons (DB connection, HTTP client, LLM client) at module level.
Pass dependencies explicitly via constructor or function parameter. This is what
makes units testable without monkey-patching.

```python
# ✅ correct — injectable, testable
class Enricher:
    def __init__(self, db: Database, discogs: DiscogsClient, llm: LLMClient) -> None:
        self._db = db
        self._discogs = discogs
        self._llm = llm

# ❌ wrong — untestable global coupling
import db  # module-level singleton
```

### 5. Explicit Error Handling — No Bare Excepts

Every `except` clause must name a specific exception type. Use custom exception
classes (defined in `tagger/exceptions.py`) for domain errors so callers can
handle them precisely.

```python
# ✅ correct
except httpx.HTTPStatusError as exc:
    if exc.response.status_code == 429:
        raise RateLimitError(service="discogs") from exc

# ❌ wrong
except Exception:
    pass
```

### 6. Structured Logging — `structlog`

Use `structlog` throughout. Never use `print()` for diagnostic output. Every log
call must include relevant context fields.

```python
import structlog
log = structlog.get_logger(__name__)

log.info("discogs.search", album=album_guess, artist=artist_guess, result_count=len(results))
log.warning("discogs.no_match", album_id=album_id, score=best_score, threshold=threshold)
log.error("writer.permission_denied", file_path=str(path), exc_info=True)
```

Configure `structlog` in `tagger/logging_config.py` to emit JSON in CI/production
and pretty-printed console output in development (detected via `CI` env var).

### 7. Configuration via Pydantic Settings

Use `pydantic-settings` (`BaseSettings`) for all runtime configuration. No raw
`os.environ` or `os.getenv` calls outside `config.py`.

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    discogs_token: str
    library_root: Path
    working_dir: Path = Path("./tagger_workdir")
    workers: int = 4
    discogs_fuzzy_threshold: int = 85
    id3_version: Literal["2.3", "2.4"] = "2.3"
    llm_provider: Literal["claude", "gemini"] = "claude"
```

### 8. SQLite Access via a Repository Pattern

All SQL lives in `tagger/db/` — never inline SQL strings in business logic.
Use a `Repository` class per domain entity (`AlbumRepository`, `TrackRepository`,
`ManualReviewRepository`). Repositories accept a `sqlite3.Connection` injected at
construction time.

```python
class AlbumRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(self, album: AlbumRecord) -> None: ...
    def pending(self) -> list[AlbumRecord]: ...
    def mark_found(self, album_id: int, release_id: int) -> None: ...
```

Use `conn.execute` with parameterised queries (`?` placeholders). No f-string SQL.

### 9. Concurrency — ThreadPoolExecutor with Structured Shutdown

All parallel work uses `concurrent.futures.ThreadPoolExecutor` (I/O-bound).
Always use the context manager form so the executor shuts down cleanly on
interrupt or exception.

```python
with ThreadPoolExecutor(max_workers=settings.workers) as pool:
    futures = {pool.submit(enrich_album, album): album for album in pending}
    for future in as_completed(futures):
        album = futures[future]
        try:
            future.result()
        except EnrichmentError as exc:
            log.error("enrich.failed", album_id=album.id, error=str(exc))
```

Handle `KeyboardInterrupt` at the top-level CLI entry point to ensure the executor
calls `shutdown(wait=True, cancel_futures=True)` before exit.

### 10. Retry Decorator with Exponential Back-off

Define a reusable `@retry` decorator in `tagger/utils/retry.py` using `tenacity`.
Apply it to all external API calls (Discogs, Wikipedia, LLM).

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    retry=retry_if_exception_type(RateLimitError),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(5),
)
def search_discogs(self, artist: str, album: str) -> list[DiscogsRelease]: ...
```

---

## Project Layout

Maintain this exact structure. Do not create files outside it without asking.

```
mp3-enricher/
├── CLAUDE.md                     ← this file
├── GEMINI.md                     ← Gemini Code Assist instructions
├── README.md
├── pyproject.toml                ← single source of truth for deps + tooling
├── .env.example
├── .github/
│   └── workflows/
│       ├── ci.yml                ← lint + test on every push/PR
│       └── release.yml           ← tag-triggered PyPI publish (future)
├── tagger/
│   ├── __init__.py
│   ├── cli.py                    ← Click entrypoint; thin — no business logic
│   ├── mp3_tagger.py             ← actual CLI commands (enrich, write, scan-integrity, etc.)
│   ├── config.py                 ← pydantic-settings Settings class
│   ├── exceptions.py             ← all custom exception classes
│   ├── logging_config.py         ← structlog setup
│   ├── db/
│   │   ├── __init__.py
│   │   ├── connection.py         ← connection factory, migration runner
│   │   ├── migrations/
│   │   │   ├── 001_initial.sql
│   │   │   ├── 002_add_compilation.sql
│   │   │   ├── 002_add_disc_number.sql
│   │   │   └── 003_add_tag_issues.sql  ← tag_issues table (scan-integrity)
│   │   ├── album_repo.py
│   │   ├── track_repo.py
│   │   ├── tag_issues_repo.py    ← TagIssuesRepository (upsert_batch, get_pending, resolve)
│   │   └── manual_review_repo.py
│   ├── integrity/                ← NEW: tag integrity scanning (issue #66)
│   │   ├── __init__.py
│   │   ├── models.py             ← TagIssue (Pydantic), IssueKind (StrEnum)
│   │   └── scanner.py            ← IntegrityScanner.scan_library()
│   ├── scanner/
│   │   ├── __init__.py
│   │   ├── walker.py             ← recursive directory walk
│   │   ├── id3_reader.py         ← mutagen baseline tag read
│   │   └── folder_parser.py      ← artist/album extraction from folder name
│   ├── enricher/
│   │   ├── __init__.py
│   │   ├── pipeline.py           ← orchestrates per-album enrichment
│   │   ├── prefill.py            ← NEW: best_master_url, prefill_pass1/2 (issue #66)
│   │   ├── discogs/
│   │   │   ├── __init__.py
│   │   │   ├── client.py         ← API wrapper
│   │   │   ├── release_selector.py  ← oldest-release logic
│   │   │   └── art_downloader.py
│   │   ├── web/
│   │   │   ├── __init__.py
│   │   │   └── scraper.py        ← Wikipedia / AllMusic fetch + parse
│   │   ├── llm/
│   │   │   ├── __init__.py
│   │   │   ├── base.py           ← LLMClient protocol / ABC
│   │   │   ├── claude_client.py
│   │   │   ├── gemini_client.py
│   │   │   └── prompt_builder.py
│   │   └── formatter.py          ← title normalisation, bracket → paren, feat. extraction
│   ├── writer/
│   │   ├── __init__.py
│   │   └── id3_writer.py         ← mutagen write, dry-run support
│   ├── manual/
│   │   ├── __init__.py
│   │   └── csv_handler.py        ← read/write manual_review.csv
│   └── utils/
│       ├── __init__.py
│       ├── retry.py              ← tenacity-based retry decorator
│       └── rate_limiter.py       ← token-bucket rate limiter
└── tests/
    ├── conftest.py               ← shared fixtures: tmp DB, mock clients, sample MP3s
    ├── unit/
    │   ├── scanner/
    │   │   ├── test_walker.py
    │   │   ├── test_id3_reader.py
    │   │   └── test_folder_parser.py
    │   ├── enricher/
    │   │   ├── discogs/
    │   │   │   ├── test_client.py
    │   │   │   ├── test_release_selector.py
    │   │   │   └── test_art_downloader.py
    │   │   ├── llm/
    │   │   │   ├── test_prompt_builder.py
    │   │   │   └── test_response_parser.py
    │   │   └── test_formatter.py
    │   ├── writer/
    │   │   └── test_id3_writer.py
    │   └── utils/
    │       ├── test_retry.py
    │       └── test_rate_limiter.py
    ├── integration/
    │   ├── test_scan_phase.py     ← real SQLite, real filesystem (tmp dir)
    │   ├── test_enrich_phase.py   ← real SQLite, mocked HTTP
    │   └── test_write_phase.py    ← real SQLite, real MP3 fixtures
    └── fixtures/
        ├── sample_library/        ← tiny MP3 files for integration tests
        │   └── Artist - Album/
        │       ├── 01 - Track.mp3
        │       └── 02 - Track.mp3
        ├── discogs_responses/     ← JSON cassettes for HTTP mocking
        └── wiki_pages/            ← HTML snapshots for scraper tests
```

---

## Testing Standards

### Test File Naming

- Unit tests mirror the module path: `tagger/enricher/formatter.py` →
  `tests/unit/enricher/test_formatter.py`.
- Integration tests are named for the phase they cover.
- Every test file starts with a docstring explaining what module/class it covers.

### pytest Configuration (`pyproject.toml`)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = [
    "--strict-markers",
    "--strict-config",
    "-ra",
    "--tb=short",
    "--cov=tagger",
    "--cov-report=term-missing",
    "--cov-report=html:htmlcov",
    "--cov-fail-under=90",
]
markers = [
    "unit: fast, no I/O",
    "integration: real filesystem or DB",
    "slow: tests that call live APIs (skipped in CI unless LIVE_APIS=1)",
]
```

Coverage must stay **at or above 90%**. The CI gate will fail below this threshold.

### Fixture Strategy

- **Unit tests** use `unittest.mock.MagicMock` / `pytest-mock`'s `mocker` fixture
  for all external dependencies (DB, HTTP, filesystem).
- **Integration tests** use `tmp_path` (pytest built-in) for real filesystem
  operations, and `pytest-httpx` for mocking HTTP responses from recorded cassettes.
- **Never call live APIs in tests.** Gate any test that needs a live API behind
  `@pytest.mark.slow` and a `LIVE_APIS` environment variable check.
- Create minimal real MP3 files for integration tests using `mutagen` in a
  `conftest.py` fixture — do not commit large binary files.

```python
# conftest.py example
@pytest.fixture()
def sample_mp3(tmp_path: Path) -> Path:
    from mutagen.id3 import ID3, TIT2
    path = tmp_path / "track.mp3"
    # write minimal valid MP3 header bytes
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 192)
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Original Title"))
    tags.save(str(path))
    return path
```

### What to Test

For every module, tests must cover:

| Category | Examples |
|---|---|
| Happy path | Correct input → correct output |
| Edge cases | Empty list, zero tracks, missing fields, non-ASCII filenames |
| Idempotency | Running the same operation twice produces the same result |
| Error paths | API 429, 500, connection timeout, corrupt file, permission denied |
| Boundary validation | Pydantic model rejects bad data, SQL rejects NULL in NOT NULL columns |

### Formatter Tests Must Be Exhaustive

The title/artist formatter has the most edge cases. Cover all of these explicitly:

```python
@pytest.mark.parametrize("raw, expected", [
    ("Song Title [feat. Guest]",     "Song Title (feat. Guest)"),
    ("Song Title {feat. Guest}",     "Song Title (feat. Guest)"),
    ("Song Title [Remix]",           "Song Title (Remix)"),
    ("Song Title (Remix)",           "Song Title (Remix)"),   # already correct
    ("Song Title [feat. A] [Remix]", "Song Title (feat. A) (Remix)"),
    ("SONG TITLE",                   "Song Title"),            # title-case normalisation
    ("song title (feat. guest)",     "Song Title (feat. Guest)"),
])
def test_normalize_title(raw: str, expected: str) -> None:
    assert normalize_title(raw) == expected
```

---

## Code Style & Tooling

All enforced via `pre-commit` and CI. Never disable a linter rule without a comment
explaining why.

### `pyproject.toml` Tooling Block

```toml
[tool.ruff]
target-version = "py311"
line-length = 100
select = [
    "E", "W",    # pycodestyle
    "F",         # pyflakes
    "I",         # isort
    "N",         # pep8-naming
    "UP",        # pyupgrade
    "B",         # flake8-bugbear
    "C4",        # flake8-comprehensions
    "SIM",       # flake8-simplify
    "TCH",       # flake8-type-checking
    "ANN",       # flake8-annotations
    "PT",        # flake8-pytest-style
    "RUF",       # ruff-specific
]
ignore = ["ANN101", "ANN102"]   # skip self/cls annotations

[tool.ruff.isort]
known-first-party = ["tagger"]

[tool.mypy]
python_version = "3.11"
strict = true
warn_unused_ignores = true
disallow_untyped_defs = true
plugins = ["pydantic.mypy"]

[tool.coverage.run]
branch = true
source = ["tagger"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
]
```

### `.pre-commit-config.yaml`

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.4.4
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.10.0
    hooks:
      - id: mypy
        additional_dependencies: [pydantic, types-all]
```

---

## CI Pipeline (`.github/workflows/ci.yml`)

Every push and pull request must pass all of the following:

```yaml
jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: ruff format --check .
      - run: mypy tagger/
      - run: pytest tests/unit tests/integration -m "not slow"
        env:
          CI: "1"
```

**PRs may not be merged unless CI is green.** No `# type: ignore` without a comment.
No `# noqa` without a comment.

---

## Git Workflow

### Branch Naming

```
feat/<short-description>     # new feature
fix/<short-description>      # bug fix
test/<short-description>     # adding/fixing tests only
refactor/<short-description> # no behaviour change
chore/<short-description>    # deps, CI, tooling
```

### Commit Message Format (Conventional Commits)

```
<type>(<scope>): <short imperative summary>

[optional body: what and why, not how]

[optional footer: BREAKING CHANGE, closes #issue]
```

Types: `feat`, `fix`, `test`, `refactor`, `chore`, `docs`, `perf`
Scopes: `scanner`, `discogs`, `llm`, `writer`, `db`, `cli`, `ci`

Examples:
```
feat(discogs): select oldest release across all master versions
test(formatter): add parametrized bracket-to-paren normalization cases
fix(writer): handle PermissionError on read-only MP3 and record to DB
```

### Pull Request Rules

- Every PR must include tests for all changed behaviour.
- PR description must include a **"Test Plan"** section listing what was tested.
- Squash-merge to `main`. Linear history only.
- Reference the relevant section of the brief in the PR description.
- **The pre-push hook (`.githooks/pre-push`) runs the full CI gate automatically.**
  After cloning, activate it once with:
  ```bash
  git config core.hooksPath .githooks
  ```
  The hook runs: `ruff check`, `ruff format --check`, `mypy tagger/`, `pytest`.
  If it fails, fix the issue before pushing.  To skip in a genuine emergency: `git push --no-verify`.

---

## Known Enrichment Failure Modes

Understanding these helps when debugging enrichment results or writing new match logic.

### Multi-Disc Albums: Track Title Shifting
**Root cause:** Positional fallback (Strategy 4 in `_match_discogs_track`) assigns the nth file
to the nth Discogs track. On a multi-disc album, disc-2 files sort after disc-1 files, so
file `2-01` becomes index 13 (or whatever) and gets assigned the wrong Discogs track.

**Fix:** Strategy 0 parses the `N-NN` filename prefix and maps it directly to Discogs position
`"N-M"` (e.g. `2-01 Song.mp3` → Discogs position `"2-1"`). See `pipeline._match_discogs_track`.

**Affected albums:** Any 2+ disc CD where files use `N-NN Track.mp3` naming.

### Wrong Artist Match (Cat Stevens / Astrud Gilberto class of bug)
**Root cause:** The Discogs fuzzy search can return a result whose title matches the album name
but is by a completely different artist, especially for common album titles.

**Fix:** After fetching the release, `enrich_album` checks
`fuzz.token_set_ratio(folder_artist, release_artist)`. If similarity < 40, the match is
rejected and added to manual review. Various Artists compilations are exempt.

See `pipeline._ARTIST_SIMILARITY_FLOOR = 40` and the guard block in `enrich_album`.

### ID3 Tag Key Conventions
- **APIC (album art):** key is `"APIC:"` (type=3, desc=""), NOT `"APIC:Cover"`.
- **Grouping:** written to BOTH `TIT1` (standard) and `GRP1` (iTunes 12.9.1+). Both always
  get the **same** value from `EnrichmentData.to_grp1()`.
- **GRP1/TIT1 format:** `Origin:City, Country | Gender:Male | Subgenre:X, Y | Label:Z | link:Wu-Tang Clan`
  (pipe-separated key:value pairs from `EnrichmentData.to_grp1()`). The collective/affiliation
  key is `link:` (short form). Multiple collectives are comma-separated within one segment:
  `link:Wu-Tang Clan, Gravediggaz`.
- **TPOS (disc number):** written from `TrackRecord.disc_number` when set. Populated by the
  enricher from the Discogs position string (e.g. `"2-1"` → disc 2).

### Windows SMB Temp-File Performance
When mutagen's in-place save fails on an SMB share (`OSError EINVAL`), the writer falls back
to `_save_tags_via_temp`. The temp file is created in `Path(file_path).parent` — same drive
as the target — to avoid a slow cross-drive `shutil.move`. Creating temp files in the
system temp dir (`%TEMP%`) on a different drive would require a full network copy twice.

---

## Operational Utilities — CLI Commands and Claude Skills (issue #66)

These four commands were promoted from ad-hoc `scripts/` into the core CLI. Each has
a corresponding Claude Code skill (`.claude/skills/<name>/SKILL.md`).

### `scan-integrity <library_path>`

Detects ID3 tag / folder-name mismatches. Run this to surface problems **before**
enrichment so bad tags don't corrupt the enrichment output.

```bash
python tagger/mp3_tagger.py scan-integrity "/path/to/your/music" --db-path library.db
```

- Writes findings to `<library>/_tag_issues.csv` (configurable with `--out`)
- Persists to the `tag_issues` table in the DB (use `--no-db` to skip)
- Re-running is idempotent — duplicate rows are silently ignored
- Claude skill: `/scan-integrity` (scan only) or `/scan-integrity upload` (scan + append to Google Sheet)

**Key types of issues detected:**

| `issue_kind` | Description |
|---|---|
| `album_artist_mismatch` | TPE2 doesn't fuzzy-match the artist folder |
| `album_mismatch` | TALB doesn't fuzzy-match the album folder |
| `inconsistent_album_artist` | TPE2 differs across tracks in the same folder |
| `inconsistent_album` | TALB differs across tracks in the same folder |
| `compilation_artist` | Real artist in TPE2 but "Various" in TPE1 inside a catch-all folder |
| `all_untitled` | ≥80% of tracks have blank/generic titles |
| `track_title` | Filename suggests a different title than TIT2 (only surfaced when album has other issues) |

**Relevant modules:** `tagger/integrity/scanner.py`, `tagger/integrity/models.py`,
`tagger/db/tag_issues_repo.py`, `tagger/db/migrations/003_add_tag_issues.sql`

---

### `retry-not-found`

Re-runs the Discogs enrichment pipeline on all albums with `enrichment_status=not_found`.
Useful after network outages, token changes, or heuristic improvements.

```bash
python tagger/mp3_tagger.py retry-not-found --db-path library.db
python tagger/mp3_tagger.py retry-not-found --db-path library.db --starts-with R
python tagger/mp3_tagger.py retry-not-found --db-path library.db --dry-run
```

Unlike the old `scripts/retry_not_found.py`, this calls the pipeline **directly** —
no subprocess. Prints `Newly found: N | Still not_found: M` at the end.

- Claude skill: `/retry-not-found` or `/retry-not-found <prefix>`

---

### `prefill-master-urls --csv <path>`

Pre-fills `user_discogs_url` in a manual-review CSV before the human reviewer opens it,
cutting down lookup work.

```bash
python tagger/mp3_tagger.py prefill-master-urls --csv manual_review.csv --db-path library.db
```

Two passes are run in sequence:
1. **Pass 1** — "No Discogs version with N tracks" rows: re-searches Discogs, writes master URL
2. **Pass 2** — "No Discogs match" rows: tries a search using the per-track artist tag when it
   differs significantly from the album-artist folder name (catches aliases, side projects)

The CSV is updated **in place**. Rows that already have a URL are untouched.

**Relevant module:** `tagger/enricher/prefill.py` (`best_master_url`, `prefill_pass1/2`)

- Claude skill: `/prefill-master-urls <csv>`

---

### `enrich-missing --csv <path>`

Scans artist directories for albums referenced in a reviewed CSV that are not yet in
the database. Use this before `process-manual` when the DB was rebuilt or the reviewer
is working from a different machine.

```bash
python tagger/mp3_tagger.py enrich-missing --csv reviewed_manual.csv --db-path library.db
```

Workflow:
1. Reads the CSV, finds `folder_path` entries not in the DB whose parent dir exists on disk
2. Scans each artist directory (scan + Discogs enrichment)
3. Does NOT apply user-supplied Discogs URLs — run `process-manual` afterwards for that

- Claude skill: `/enrich-missing <csv>`

---

### Claude Skills Summary

All four commands are available as Claude Code slash commands:

| Slash command | Maps to CLI command |
|---|---|
| `/scan-integrity [upload]` | `scan-integrity "/path/to/your/music"` |
| `/retry-not-found [prefix]` | `retry-not-found [--starts-with prefix]` |
| `/prefill-master-urls <csv>` | `prefill-master-urls --csv <csv>` |
| `/enrich-missing <csv>` | `enrich-missing --csv <csv>` |

The `/tag-sheet` skill also references these commands in its "Related Skills" section so
Claude knows when to suggest running them as part of the tag-sheet workflow.

---

## Claude-Specific Instructions

When generating code for this project:

1. **Always read the relevant test file** (or create it first) before writing
   implementation code.
2. **Prefer composition over inheritance.** Use `Protocol` for interfaces so
   mock implementations in tests are trivial.
3. **Small, focused functions.** If a function exceeds ~30 lines, ask whether
   it should be split.
4. **No business logic in `cli.py`.** The CLI layer parses arguments, builds
   the dependency graph, and calls service-layer functions. That is all.
5. **All DB writes must be wrapped in transactions.** Use `with conn:` (Python
   sqlite3 context manager) so writes are atomic and rolled back on exception.
6. **When adding a new LLM provider**, implement the `LLMClient` Protocol in
   `tagger/enricher/llm/base.py` — do not add conditionals to existing callers.
7. **SQL migrations are versioned.** New schema changes get a new file in
   `tagger/db/migrations/` (e.g., `002_add_art_cache.sql`). The migration
   runner in `connection.py` applies unapplied migrations on startup.
8. **When uncertain about a requirement**, ask a clarifying question rather than
   assuming. Prefer a short question over a wrong implementation.

---

## Dependency Reference (`pyproject.toml` `[project.dependencies]`)

```toml
dependencies = [
    "click>=8.1",
    "pydantic>=2.7",
    "pydantic-settings>=2.2",
    "structlog>=24.1",
    "httpx>=0.27",
    "beautifulsoup4>=4.12",
    "mutagen>=1.47",
    "python3-discogs-client>=2.6",
    "rapidfuzz>=3.9",
    "tenacity>=8.3",
    "anthropic>=0.28",       # Claude client
    "google-generativeai>=0.7",  # Gemini client
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2",
    "pytest-cov>=5.0",
    "pytest-mock>=3.14",
    "pytest-httpx>=0.30",
    "pytest-xdist>=3.5",   # parallel test execution: pytest -n auto
    "ruff>=0.4",
    "mypy>=1.10",
    "pre-commit>=3.7",
]
```

---

## Windows & PowerShell Development Guidelines

When working in this environment (detected via `win32`), adhere to these CLI patterns:

1. **Command Separators:** Use `;` instead of `&&` to chain commands in PowerShell.
   - ✅ `pytest ; ruff check .`
   - ❌ `pytest && ruff check .`
2. **Path handling:** Use backslashes `\` for shell commands but prefer forward slashes `/` or `pathlib.Path` within Python code for cross-platform compatibility.
3. **Environment Variables:** Use `$env:VAR_NAME = "value"` for session-level variables if running manual checks, though the tool handles `.env` via `pydantic-settings`.
4. **Encoding:** Ensure all new files are written with `UTF-8` encoding to avoid BOM or shift-JIS issues on Windows.

---

## Checklist Before Marking a Task Complete

- [ ] Test written first and fails before implementation
- [ ] Implementation makes the test pass
- [ ] `mypy tagger/` reports no errors — **run this before every commit**
- [ ] `ruff check .` reports no errors — **run this before every commit**
- [ ] `ruff format --check .` reports no files to reformat — **run this before every commit**
- [ ] `pytest --cov=tagger --cov-fail-under=90` passes — **run this before every commit; coverage must stay ≥ 90%**
- [ ] No `print()` statements (use `structlog`)
- [ ] No bare `except:` or `except Exception:`
- [ ] No inline SQL outside a Repository class
- [ ] All new public functions have type annotations and a docstring
- [ ] `CHANGELOG.md` updated if user-visible behaviour changed
- [ ] **Before pushing: the pre-push hook (`.githooks/pre-push`) runs CI automatically — ensure `git config core.hooksPath .githooks` is set in your clone**
