-- Migration 004: add origin column to sessions to distinguish
-- how a session was created: NULL = original, 'fork' = forked, 'derive' = derived.
ALTER TABLE sessions ADD COLUMN origin TEXT;

INSERT INTO schema_version (version, applied_at) VALUES (4, datetime('now'));
