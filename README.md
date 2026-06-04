# mp3-enricher

[![CI](https://github.com/jschloman/mp3-enricher/actions/workflows/ci.yml/badge.svg)](https://github.com/jschloman/mp3-enricher/actions/workflows/ci.yml)

A parallelised, idempotent CLI pipeline that enriches ID3 tags on MP3 libraries using Discogs, Wikipedia, and MusicBrainz.

The tool is **non-destructive** — it never moves, renames, or deletes your files. It only writes metadata into the MP3 ID3 tags.

---

## Quick Start

```bash
git clone https://github.com/jschloman/mp3-enricher.git && cd mp3-enricher
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                                 # add your DISCOGS_TOKEN
python tagger/mp3_tagger.py enrich "/path/to/music" --db-path library.db
python tagger/mp3_tagger.py write  --db-path library.db
```

See [Step-by-step usage](#step-by-step-usage) for a full walkthrough including manual review.

---

## Table of Contents

- [How it works](#how-it-works)
- [What gets written](#what-gets-written)
- [Installation](#installation)
- [Configuration](#configuration)
- [Step-by-step usage](#step-by-step-usage)
  - [Step 1 — Enrich a folder](#step-1--enrich-a-folder)
  - [Step 2 — Write tags to disk](#step-2--write-tags-to-disk)
  - [Step 3 — Handle albums that weren't matched](#step-3--handle-albums-that-werent-matched)
- [Command reference](#command-reference)
- [Repairing corrupted tags from iTunes](#repairing-corrupted-tags-from-itunes)
- [Tips and troubleshooting](#tips-and-troubleshooting)

---

## How it works

Each album folder is processed in three stages:

```
enrich command
  1. Scan         — finds all .mp3 files, reads existing tags and folder name
  2. Discogs      — searches for the album, selects the oldest original release
  3. Wikipedia    — fetches the artist's biography for context
  4. MusicBrainz  — looks up collective/band memberships
  5. Heuristics   — infers origin, gender, genre, label, etc. from all sources
  6. DB            — saves everything to a local SQLite database

write command
  7. Write        — reads the database and applies tags to the .mp3 files
```

State is stored in a local SQLite database (`tester.db` by default). Every step is idempotent — re-running a command skips work that is already done.

---

## What gets written

The following ID3 tags are written to each MP3 file:

| Tag | Frame | Example |
|---|---|---|
| Title | `TIT2` | `Head Like A Hole` |
| Artist | `TPE1` | `Nine Inch Nails` |
| Album Artist | `TPE2` | `Nine Inch Nails` |
| Album | `TALB` | `Pretty Hate Machine` |
| Genre | `TCON` | `Industrial` |
| Track number | `TRCK` | `01/10` |
| Year | `TDRC` / `TYER` | `1989` |
| Grouping | `TIT1` | `Origin:Cleveland, US \| Gender:Male \| Label:TVT Records` |

The **Grouping** tag (`TIT1`) is a structured metadata string that carries everything the heuristic enricher inferred, in a pipe-delimited format:

```
Origin:Cleveland, US | Gender:Male | Race:White | Subgenre:Industrial Rock | Label:TVT Records | link:Nine Inch Nails | Holiday:Halloween
```

The `link:` segment lists artist collectives or band affiliations found via MusicBrainz. Multiple links are comma-separated (`link:Wu-Tang Clan, Gravediggaz`). The segment is omitted when no links are found.

---

## Installation

```bash
git clone https://github.com/jschloman/mp3-enricher
cd mp3-enricher
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

---

## Configuration

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Open `.env` and fill in your Discogs token (the only required value):

```ini
DISCOGS_TOKEN=your_personal_access_token_here
```

You can get a free Discogs Personal Access Token from:
**Settings → Developers → Generate new token** at [discogs.com](https://www.discogs.com/settings/developers)

Optional settings:

```ini
DISCOGS_FUZZY_THRESHOLD=85   # Minimum match score (0–100). Lower = more permissive.
ID3_VERSION=2.3              # 2.3 (default, widest compatibility) or 2.4
WORKERS=4                    # Thread count for parallel processing
```

---

## Step-by-step usage

### Step 1 — Enrich a folder

The `enrich` command does everything except writing to the MP3 files themselves. It scans the folder, looks up the album on Discogs, scrapes Wikipedia, and saves all enriched metadata to the local database.

```bash
python -m tagger.mp3_tagger enrich "D:\Music\Nine Inch Nails - Pretty Hate Machine"
```

**The folder name matters.** The tool parses it to guess the artist and album. The expected format is:

```
Artist - Album
```

For example:
- `Nine Inch Nails - Pretty Hate Machine` → artist: *Nine Inch Nails*, album: *Pretty Hate Machine*
- `The Cure - Disintegration` → artist: *The Cure*, album: *Disintegration*

If the folder name doesn't follow this format the tool will still try, but the Discogs match will be less reliable. You can always fix a failed match via the manual review workflow (see Step 3).

**What you'll see:**

```
[*] Initializing test database at tester.db...
[*] Scanning folder: D:\Music\Nine Inch Nails - Pretty Hate Machine...
[+] Found 10 MP3 files.
[+] Guessed Artist: Nine Inch Nails
[+] Guessed Album:  Pretty Hate Machine
[+] Folder and tracks inserted into database.
[*] Starting Discogs enrichment...
[+] Best Match Found: Nine Inch Nails - Pretty Hate Machine (ID: 75544, Year: 1989)
[+] Master ID: 207276
[*] Fetching full release details...
[+] Release Title: Pretty Hate Machine
[+] Artists: Nine Inch Nails
[+] Tracks found in Discogs: 10
[*] Fetching Wikipedia context...
[+] Fetched 843 characters of Wikipedia context.
[*] Applying heuristic enrichment...
[+] Fetched Discogs artist profile for: Nine Inch Nails
[*] Querying MusicBrainz for collective memberships...
[!] No MusicBrainz collectives found.
[+] Guessed Gender: Male
[+] Guessed Origin: Cleveland, Ohio, US
[+] Guessed Genre:  Industrial
[+] Guessed Subgenres: Industrial Rock, Synth-pop
[+] Canonical Album Artist: Nine Inch Nails
[+] GRP1 Sample: Origin:Cleveland, Ohio, US | Gender:Male | Subgenre:Industrial Rock, Synth-pop | Label:TVT Records
[*] Downloading album art from: https://img.discogs.com/...
[+] Album art saved to: tagger_workdir/art/75544.jpg

[SUCCESS] Enrichment test completed.
```

If Discogs **cannot find a match**, the album is added to the manual review queue instead and you'll see:

```
[-] No suitable Discogs release found — added to manual review.
```

See [Step 3](#step-3--handle-albums-that-werent-matched) for how to handle this.

**Options:**

```
--db-path PATH    Path to the SQLite database. Default: tester.db
--token TEXT      Discogs token (overrides DISCOGS_TOKEN in .env)
```

---

### Step 2 — Write tags to disk

Once enrichment is done, run `write` to apply the saved metadata to your MP3 files:

```bash
python -m tagger.mp3_tagger write
```

This reads all tracks in the database with `enrichment_status=found` and `written_status=pending` and writes their tags to disk. It skips anything already written, so it's safe to run repeatedly.

**What you'll see:**

```
[+] Written: 10 | Errors: 0
```

**Preview before committing** — use `--dry-run` to see what would happen without touching any files:

```bash
python -m tagger.mp3_tagger write --dry-run
```

```
[*] Dry-run mode — no files will be modified.
[+] Written: 10 | Errors: 0
```

**Re-write already-written tracks** — if you've updated the enrichment data and need to re-apply:

```bash
python -m tagger.mp3_tagger write --force
```

**Speed up large libraries** — process tracks in parallel:

```bash
python -m tagger.mp3_tagger write --workers 4
```

**Limit to one folder** — write only a single artist or album directory:

```bash
python -m tagger.mp3_tagger write --folder "/path/to/music/Nine Inch Nails"
python -m tagger.mp3_tagger write --folder "/path/to/music/Nine Inch Nails/Pretty Hate Machine"
```

**Options:**

```
--db-path PATH            SQLite database to read from. Default: tester.db
--dry-run                 Preview writes without modifying any files
--force                   Re-write tracks already marked as done
--workers INTEGER         Number of parallel write threads. Default: 1
--id3-version [2.3|2.4]   ID3 version to write. Default: 2.3
--folder PATH             Limit writes to tracks whose file path starts with PATH
```

> **ID3 version note:** `2.3` is the default and has the widest software compatibility (iTunes, Windows Explorer, most car stereos). Use `2.4` if your player specifically requires it — it stores the year in `TDRC` instead of `TYER`.

---

### Step 3 — Handle albums that weren't matched

When Discogs can't automatically find an album — usually because the folder name is ambiguous, the release is rare, or the artist name is spelled differently — the album is flagged for manual review.

#### 3a. Export the review list to CSV

```bash
python -m tagger.mp3_tagger export-manual
```

This creates `manual_review.csv` with one row per unmatched album:

```csv
album_id,folder_path,artist_guess,album_guess,reason,user_discogs_url
12,D:\Music\NIN - PHM,NIN,PHM,No Discogs match,
47,D:\Music\Unknown Artist - Untitled,Unknown Artist,Untitled,No Discogs match,
```

**Options:**

```
--db-path PATH       Database to read from. Default: tester.db
--csv-path PATH      Output file. Default: manual_review.csv
```

#### 3b. Fill in the Discogs URLs

Open `manual_review.csv` in Excel, Numbers, or any text editor.

For each album you can identify, find it on [discogs.com](https://www.discogs.com) and paste the Discogs URL into the `user_discogs_url` column. Leave rows blank that you can't identify.

```csv
album_id,folder_path,artist_guess,album_guess,reason,user_discogs_url
12,D:\Music\NIN - PHM,NIN,PHM,No Discogs match,https://www.discogs.com/Nine-Inch-Nails-Pretty-Hate-Machine/release/75544
13,D:\Music\Portishead\Dummy,Portishead,Dummy,No Discogs version with 11 tracks,https://www.discogs.com/master/22702-Portishead-Dummy
47,D:\Music\Unknown Artist - Untitled,Unknown Artist,Untitled,No Discogs match,
```

Both **release URLs** and **master URLs** are accepted:

| URL format | Example | Behaviour |
|---|---|---|
| `/release/N` | `https://www.discogs.com/release/75544` | Uses that exact release |
| `/Artist-Album/release/N` | `.../Nine-Inch-Nails.../release/75544` | Uses that exact release |
| `/master/N` | `https://www.discogs.com/master/22702` | Fetches all versions of the master and uses the oldest audio release |
| `/master/N-Slug` | `.../master/22702-Portishead-Dummy` | Same as above — the slug after the ID is ignored |

When you paste a master URL the tool automatically resolves it to the oldest original audio release (skipping video formats such as DVD and VHS), so you don't need to hunt for a specific pressing.

#### 3c. Process the corrections

```bash
python -m tagger.mp3_tagger process-manual
```

This reads every row with a `user_discogs_url`, fetches that release from Discogs, runs the full heuristic enrichment, and saves the results to the database — identical to what `enrich` does automatically.

```
[+] Processing 1 correction(s)...
  [*] album_id=12 (NIN — PHM)
  [+] Done — Pretty Hate Machine | GRP1: Origin:Cleveland, US | Gender:Male | Label:TVT Records

[SUCCESS] Manual processing complete.
```

After this, run `write` to apply the newly enriched tags to disk:

```bash
python -m tagger.mp3_tagger write
```

**Options:**

```
--db-path PATH    Database to read from. Default: tester.db
--csv-path PATH   CSV file to process. Default: manual_review.csv
--token TEXT      Discogs token (overrides DISCOGS_TOKEN in .env)
```

---

## Command reference

### Core pipeline

| Command | What it does |
|---|---|
| `enrich <folder>` | Scan a folder, look up Discogs/Wikipedia/MusicBrainz, save results to DB |
| `write` | Apply enriched tags from DB to MP3 files on disk |
| `export-manual` | Export unmatched albums to `manual_review.csv` for human review |
| `process-manual` | Re-run enrichment using Discogs URLs you filled in to the CSV |
| `fix <folder>` | Re-enrich already-matched albums (e.g. after a bug fix) and reset written status |
| `fix-track-numbers <library>` | Repair TRCK/TPOS tags from filename prefixes (`01 - Song.mp3`) |

### Operational utilities

| Command | What it does |
|---|---|
| `scan-integrity <library>` | Scan for tag/folder mismatches; write CSV + DB |
| `retry-not-found` | Re-run enrichment on all `not_found` albums in DB |
| `prefill-master-urls --csv <file>` | Pre-fill Discogs URLs in a manual-review CSV |
| `enrich-missing --csv <file>` | Scan artist dirs missing from DB (run before `process-manual`) |
| `audit-itunes --library <path> --itunes-xml <path>` | Compare current MP3 tags against iTunes Library XML; write discrepancy CSV |
| `restore-from-itunes --library <path> --itunes-xml <path>` | Restore Artist, Album Artist, Album, and Track Number from iTunes ground truth |

All commands share `--db-path` to point at a specific database file. The default is `tester.db` in the current directory.

#### `scan-integrity` — detect tag problems before they cause bad enrichment

```bash
python tagger/mp3_tagger.py scan-integrity "/path/to/your/music" \
    --db-path library.db \
    --out _tag_issues.csv \
    --threshold 75
```

Walks the library tree and flags:
- **AlbumArtist** (`TPE2`) or **Album** (`TALB`) tag that doesn't match the folder name (fuzzy threshold, default 75)
- **Inconsistent** tags across tracks in the same album folder
- **All-untitled** albums (≥80% of tracks have blank/generic titles like "Track 1")
- **Compilation artist mismatch** — real AlbumArtist but TPE1="Various Artists" in a catch-all folder

Findings are written to `_tag_issues.csv` **and** to the `tag_issues` table in the database. Re-running is idempotent — duplicates are silently ignored.

```
Options:
  --db-path PATH       Database (default: tester.db)
  --out PATH           Output CSV (default: <library>/_tag_issues.csv)
  --threshold INT      Fuzzy-match threshold 0–100 (default: 75)
  --no-db              Write CSV only — skip the database write
```

#### `retry-not-found` — retry automatic enrichment on failed albums

```bash
python tagger/mp3_tagger.py retry-not-found --db-path library.db
```

Queries the database for albums with `enrichment_status=not_found`, groups them by artist directory, and re-runs the full enrichment pipeline directly (no subprocess). Reports `Newly found: N | Still not_found: M` at the end.

```
Options:
  --db-path PATH       Database (default: tester.db)
  --token TEXT         Discogs token (overrides .env)
  --starts-with TEXT   Only retry albums whose artist folder begins with this prefix
  --dry-run            Show what would be retried without running enrichment
```

#### `prefill-master-urls` — reduce manual-review workload before human review

```bash
python tagger/mp3_tagger.py prefill-master-urls \
    --csv manual_review.csv \
    --db-path library.db
```

Runs two search passes on your manual-review CSV and fills `user_discogs_url` automatically where confident:

- **Pass 1** — rows where reason = "No Discogs version with N tracks": re-searches Discogs and writes the master URL
- **Pass 2** — rows where reason = "No Discogs match": checks whether the per-track Artist tag differs from the folder artist; if so, tries a search with the track artist (catches artist aliases and side projects)

The CSV is updated in place. Rows that already have a URL are untouched.

```
Options:
  --csv PATH           Manual review CSV to pre-fill (required)
  --db-path PATH       Database (default: tester.db)
  --token TEXT         Discogs token (overrides .env)
```

#### `enrich-missing` — populate DB for albums already identified by a reviewer

```bash
python tagger/mp3_tagger.py enrich-missing \
    --csv reviewed_manual.csv \
    --db-path library.db
```

Use this when `process-manual` reports "folder_path not in DB" for albums that clearly exist on disk. It reads the CSV, finds `folder_path` entries not yet in the database, and scans the parent artist directories to add them. Then run `process-manual` as normal to apply the reviewer-supplied Discogs URLs.

```
Options:
  --csv PATH           Reviewed manual review CSV (required)
  --db-path PATH       Database (default: tester.db)
  --token TEXT         Discogs token (overrides .env)
```

---

## Repairing corrupted tags from iTunes

If a previous enrichment run overwrote Artist, Album Artist, Album, or track numbers with wrong values, you can use your iTunes library as a ground-truth reference to find and fix the damage. This workflow requires an exported iTunes Music Library XML file.

### Where to find your iTunes XML

In iTunes / Music on Windows:
**Edit → Preferences → Advanced → "iTunes Media folder location"**. The XML file (`iTunes Music Library.xml`) lives in the same folder as your `.itl` file — typically:

```
C:\Users\<you>\Music\iTunes\iTunes Music Library.xml
```

If the file doesn't exist, open iTunes, go to **File → Library → Export Library…** and save it as XML.

### Step 1 — Audit (find the discrepancies)

```powershell
python tagger/mp3_tagger.py audit-itunes `
    --library "M:\Shared Music" `
    --itunes-xml "C:\Users\you\Music\iTunes\iTunes Music Library.xml" `
    --out itunes_audit.csv
```

This walks your library, reads each MP3's current ID3 tags, and fuzzy-matches them against the iTunes record for the same file. It writes `itunes_audit.csv` with one row per file that has at least one problem.

**Output columns:**

| Column | Description |
|---|---|
| `file_path` | Full path to the MP3 |
| `mp3_artist` / `itunes_artist` | Current tag vs iTunes value |
| `mp3_album_artist` / `itunes_album_artist` | Current tag vs iTunes value |
| `mp3_album` / `itunes_album` | Current tag vs iTunes value |
| `mp3_track_number` / `itunes_track_number` | Current tag vs iTunes value |
| `issues` | Pipe-separated list: `artist_mismatch`, `album_artist_mismatch`, `album_mismatch`, `missing_track_number`, `itunes_not_found` |
| `artist_score` / `album_score` / `album_artist_score` | Fuzzy similarity 0–100 (100 = identical) |

**Console summary example:**
```
[+] 1 247 discrepancy row(s) found.
    album_mismatch: 843
    artist_mismatch: 312
    missing_track_number: 4 102
[+] Report written to itunes_audit.csv
```

**Options:**
```
--library PATH        Music library root (required)
--itunes-xml PATH     Path to iTunes Music Library.xml (required)
--out PATH            Output CSV (default: itunes_audit.csv)
--threshold INT       Fuzzy score below which a field is flagged (default: 75)
--workers INT         Parallel tag-read threads (default: 4)
```

### Step 2 — Dry-run restore (preview the fixes)

```powershell
python tagger/mp3_tagger.py restore-from-itunes `
    --library "M:\Shared Music" `
    --itunes-xml "C:\Users\you\Music\iTunes\iTunes Music Library.xml" `
    --out restore_report.csv `
    --dry-run
```

`--dry-run` runs the full comparison and writes the report CSV showing exactly what *would* change — without modifying any files. Review `restore_report.csv` before proceeding.

**Report columns:**

| Column | Description |
|---|---|
| `file_path` | Full path to the MP3 |
| `fields_restored` | Pipe-separated list of fields that were (or would be) fixed |
| `old_artist` / `new_artist` | Before and after for the Artist tag |
| `old_album_artist` / `new_album_artist` | Before and after for Album Artist |
| `old_album` / `new_album` | Before and after for Album |
| `old_track_number` / `new_track_number` | Before and after for Track Number |
| `dry_run` | `True` when run with `--dry-run` |

### Step 3 — Apply the fixes

Once you're satisfied with the dry-run report, run without `--dry-run` to write the corrections:

```powershell
python tagger/mp3_tagger.py restore-from-itunes `
    --library "M:\Shared Music" `
    --itunes-xml "C:\Users\you\Music\iTunes\iTunes Music Library.xml" `
    --out restore_report.csv
```

Only the affected ID3 frames are overwritten. Every other tag (genre, year, grouping, artwork, etc.) is left exactly as-is.

**Track number fallback:** If iTunes has no track number for a file, the restorer attempts to parse it from the filename prefix — e.g. `03 Song.mp3` → track 3, `2-05 Song.mp3` → disc 2 track 5.

**Options:**
```
--library PATH        Music library root (required)
--itunes-xml PATH     Path to iTunes Music Library.xml (required)
--out PATH            Output report CSV (default: itunes_restore_report.csv)
--threshold INT       Fuzzy score below which a field is treated as mismatched (default: 75)
--workers INT         Parallel tag-read threads (default: 4)
--dry-run             Preview without writing any files
```

### Notes

- Files not found in the iTunes index (`itunes_not_found`) are skipped by the restorer — it only writes values it can verify against iTunes.
- Both commands are idempotent — re-running after a partial failure is safe.
- The `--threshold` option controls sensitivity. The default of 75 flags clear mismatches while ignoring minor formatting differences (e.g. `The Beatles` vs `Beatles, The`). Lower it to catch more subtle corruption; raise it if you're seeing false positives.

---

## Tips and troubleshooting

**The wrong Discogs release was selected**

The tool picks the oldest release across all master versions using fuzzy matching. If it picks the wrong one, use the manual review workflow to supply the exact Discogs URL for the correct release.

**A track wasn't updated because its position didn't match**

Track matching uses the Discogs `position` field (e.g. `"1"`, `"2"`) against the `track_number` read from the existing ID3 `TRCK` tag. If your files don't have track number tags, or the positions are in a different format (e.g. `"A1"` for vinyl sides), the track won't be enriched. Add a `TRCK` tag manually with a tool like Mp3tag and re-run.

**Discogs rate limiting**

Discogs allows 60 requests per minute for authenticated users. The tool doesn't enforce rate limiting by default in the tester CLI. If you're running large batches and hit 429 errors, wait a minute and re-run — the idempotent design means already-enriched albums are skipped.

**Re-enriching an already-processed album**

The `enrich` command uses an upsert — re-running it on a folder that's already in the database will overwrite the existing record and re-run the full lookup. This is safe.

**Checking what's in the database**

You can inspect `tester.db` directly with any SQLite viewer (e.g. [DB Browser for SQLite](https://sqlitebrowser.org/)):

```sql
-- See all albums and their status
SELECT folder_path, artist_guess, album_guess, enrichment_status FROM albums;

-- See tracks pending a write
SELECT file_path, title, artist, genre, grouping
FROM tracks
WHERE enrichment_status = 'found' AND written_status = 'pending';

-- See what's in the manual review queue
SELECT * FROM manual_reviews WHERE status = 'pending';
```
