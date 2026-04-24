-- Migration 005: rename grouping key "Collective:" to "link:"
-- Fixes rows enriched before the collective→link terminology rename (refactor commit a0dbebb).
UPDATE tracks
   SET grouping = REPLACE(grouping, 'Collective:', 'link:'),
       written_status = 'pending'
 WHERE grouping LIKE '%Collective:%';
