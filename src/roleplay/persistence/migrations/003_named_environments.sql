-- Schema version: 3
-- Named environments (environments: list in YAML scenario).
-- Each row stores one Environment entry from the EnvironmentRegistry.

CREATE TABLE IF NOT EXISTS named_environments (
    env_id      TEXT NOT NULL,
    session_id  TEXT NOT NULL REFERENCES sessions(session_id),
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    state_json  TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (env_id, session_id)
);
