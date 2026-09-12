CREATE TABLE categories (
  node TEXT PRIMARY KEY NOT NULL,
  name TEXT NOT NULL,
  label TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_categories_created_at ON categories(created_at);

PRAGMA optimize;
