-- Add indexes on the columns most frequently used in WHERE clauses.
-- All indexes use IF NOT EXISTS so re-running this migration is safe.

CREATE INDEX IF NOT EXISTS idx_albums_enrichment_status ON albums (enrichment_status);
CREATE INDEX IF NOT EXISTS idx_albums_artist_guess ON albums (artist_guess);
CREATE INDEX IF NOT EXISTS idx_tracks_written_status ON tracks (written_status);
CREATE INDEX IF NOT EXISTS idx_tracks_album_id ON tracks (album_id);
