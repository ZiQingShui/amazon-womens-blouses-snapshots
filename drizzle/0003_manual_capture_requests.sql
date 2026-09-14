CREATE TABLE capture_requests (
  id TEXT PRIMARY KEY NOT NULL,
  category_node TEXT NOT NULL,
  ranking TEXT NOT NULL CHECK (ranking IN ('new-releases', 'best-sellers')),
  requested_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
  requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at TEXT,
  completed_at TEXT,
  message TEXT,
  attempts INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX idx_capture_requests_daily_category
ON capture_requests(category_node, ranking, requested_date);

CREATE INDEX idx_capture_requests_status_requested_at
ON capture_requests(status, requested_at);

PRAGMA optimize;
