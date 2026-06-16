-- Schema version: 1
-- Applied by SqlitePersistenceLayer._run_migrations() on first connection.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT PRIMARY KEY,
    parent_session_id   TEXT,
    forked_at_episode   INTEGER,
    config_json         TEXT NOT NULL,
    started_at          TEXT NOT NULL,
    last_saved_at       TEXT NOT NULL,
    status              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parties (
    party_id        TEXT NOT NULL,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL,
    persona_json    TEXT NOT NULL,
    PRIMARY KEY (party_id, session_id)
);

CREATE TABLE IF NOT EXISTS state_changes (
    id              TEXT PRIMARY KEY,
    party_id        TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    key             TEXT NOT NULL,
    old_value_json  TEXT,
    new_value_json  TEXT NOT NULL,
    episode_index   INTEGER NOT NULL,
    reason          TEXT
);
CREATE INDEX IF NOT EXISTS idx_state_changes_party
    ON state_changes (party_id, session_id, episode_index);

CREATE TABLE IF NOT EXISTS episodes (
    episode_id              TEXT PRIMARY KEY,
    session_id              TEXT NOT NULL REFERENCES sessions(session_id),
    episode_index           INTEGER NOT NULL,
    simulated_time_start    TEXT NOT NULL,
    simulated_time_end      TEXT,
    started_at              TEXT NOT NULL,
    ended_at                TEXT,
    UNIQUE (session_id, episode_index)
);
CREATE INDEX IF NOT EXISTS idx_episodes_session
    ON episodes (session_id, episode_index);

CREATE TABLE IF NOT EXISTS turns (
    turn_id                 TEXT PRIMARY KEY,
    episode_id              TEXT NOT NULL REFERENCES episodes(episode_id),
    session_id              TEXT NOT NULL,
    party_id                TEXT NOT NULL,
    turn_index              INTEGER NOT NULL,
    output                  TEXT NOT NULL,
    state_proposals_json    TEXT NOT NULL,
    tool_calls_json         TEXT NOT NULL,
    prompt_tokens           INTEGER NOT NULL DEFAULT 0,
    completion_tokens       INTEGER NOT NULL DEFAULT 0,
    model_used              TEXT NOT NULL DEFAULT '',
    timestamp               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_episode
    ON turns (episode_id, turn_index);

CREATE TABLE IF NOT EXISTS memory_entries (
    entry_id                TEXT PRIMARY KEY,
    party_id                TEXT NOT NULL,
    session_id              TEXT NOT NULL,
    kind                    TEXT NOT NULL,
    content                 TEXT NOT NULL,
    episode_index           INTEGER NOT NULL,
    importance              REAL NOT NULL DEFAULT 1.0,
    last_accessed_episode   INTEGER NOT NULL DEFAULT 0,
    access_count            INTEGER NOT NULL DEFAULT 0,
    source_entry_ids_json   TEXT NOT NULL DEFAULT '[]',
    created_at              TEXT NOT NULL,
    forgotten               INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memory_party
    ON memory_entries (party_id, session_id, forgotten, episode_index);
CREATE INDEX IF NOT EXISTS idx_memory_importance
    ON memory_entries (party_id, session_id, importance)
    WHERE forgotten = 0;

INSERT INTO schema_version (version, applied_at) VALUES (1, datetime('now'));
