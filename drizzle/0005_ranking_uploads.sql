ALTER TABLE capture_requests ADD COLUMN ranking_source TEXT NOT NULL DEFAULT 'official';

CREATE TABLE IF NOT EXISTS ranking_uploads (
  request_id TEXT PRIMARY KEY,
  category_node TEXT NOT NULL,
  ranking TEXT NOT NULL,
  snapshot_date TEXT NOT NULL,
  source_filename TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  source_captured_at TEXT NOT NULL,
  rows_json TEXT NOT NULL,
  uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (request_id) REFERENCES capture_requests(id)
);
