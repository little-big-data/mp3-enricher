-- Tag integrity issues table for scan-integrity command

CREATE TABLE IF NOT EXISTS tag_issues (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_folder   TEXT    NOT NULL,
    album_folder    TEXT    NOT NULL,
    folder_path     TEXT,
    issue_kind      TEXT    NOT NULL,
    -- 'album_artist_mismatch' | 'album_mismatch' | 'inconsistent_album_artist'
    -- | 'inconsistent_album' | 'compilation_artist' | 'all_untitled' | 'track_title'
    detail          TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending',  -- 'pending' | 'resolved'
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tag_issues_unique
    ON tag_issues (artist_folder, album_folder, issue_kind, detail);

CREATE INDEX IF NOT EXISTS idx_tag_issues_folder
    ON tag_issues (artist_folder, album_folder);

CREATE INDEX IF NOT EXISTS idx_tag_issues_status
    ON tag_issues (status);
