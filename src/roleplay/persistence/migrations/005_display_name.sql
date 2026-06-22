-- Schema version: 5
-- Add display_name column to sessions table.

ALTER TABLE sessions ADD COLUMN display_name TEXT NOT NULL DEFAULT '';

INSERT INTO schema_version (version, applied_at)
VALUES (5, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
