-- Migration 002: add summary column to episodes table
-- Stores the AI-generated 1-2 sentence episode summary.
ALTER TABLE episodes ADD COLUMN summary TEXT NOT NULL DEFAULT '';

INSERT INTO schema_version (version, applied_at) VALUES (2, datetime('now'));
