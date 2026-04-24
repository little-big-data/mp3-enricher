-- Initial database schema for mp3-enricher

CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_path TEXT UNIQUE NOT NULL,
    artist_guess TEXT,
    album_guess TEXT,
    discogs_release_id INTEGER,
    discogs_url TEXT,
    enrichment_status TEXT DEFAULT 'pending',  -- pending | found | not_found | manual | error
    written_status TEXT DEFAULT 'pending',      -- pending | done | error
    notes TEXT
);

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    file_path TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    track_number INTEGER,
    existing_title TEXT,
    existing_artist TEXT,
    
    -- Enriched fields populated in Phase 2
    title TEXT,
    artist TEXT,
    album_artist TEXT,
    album_title TEXT,
    year INTEGER,
    track_num TEXT,   -- "N/M" format
    genre TEXT,
    grouping TEXT,   -- GRP1 composite value
    art_path TEXT,
    
    enrichment_status TEXT DEFAULT 'pending',
    written_status TEXT DEFAULT 'pending',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS manual_review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    user_discogs_url TEXT,
    resolved INTEGER DEFAULT 0
);

-- Meta table for tracking migrations
CREATE TABLE IF NOT EXISTS _migrations (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
