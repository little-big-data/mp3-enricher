-- Migration 002: add disc_number column to tracks table
-- Stores the disc number extracted from TPOS tag or multi-disc filename prefix.
-- NULL means a single-disc album (no disc position known).
ALTER TABLE tracks ADD COLUMN disc_number INTEGER;
