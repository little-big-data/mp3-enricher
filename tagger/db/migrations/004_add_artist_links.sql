-- Artist → link affiliation cache table for link-scan command

CREATE TABLE IF NOT EXISTS artist_links (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    artist_name TEXT     NOT NULL,
    link        TEXT     NOT NULL,
    source      TEXT     NOT NULL DEFAULT 'llm',  -- 'llm' | 'heuristic' | 'manual'
    confidence  REAL     NOT NULL DEFAULT 1.0,
    created_at  TEXT     NOT NULL DEFAULT (datetime('now')),
    UNIQUE(artist_name, link)
);

CREATE INDEX IF NOT EXISTS idx_artist_links_artist
    ON artist_links (artist_name);
