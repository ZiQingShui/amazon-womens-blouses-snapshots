CREATE TABLE capture_worker_status (
  id INTEGER PRIMARY KEY NOT NULL CHECK (id = 1),
  worker_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('idle', 'busy', 'error')),
  active_request_id TEXT,
  message TEXT,
  last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

PRAGMA optimize;
