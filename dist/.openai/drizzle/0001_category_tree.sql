CREATE TABLE category_nodes (
  site TEXT NOT NULL DEFAULT 'US',
  node TEXT NOT NULL,
  name TEXT NOT NULL,
  parent_node TEXT,
  depth INTEGER NOT NULL DEFAULT 0,
  path TEXT NOT NULL,
  department_slug TEXT NOT NULL DEFAULT 'fashion',
  supports_new_releases INTEGER NOT NULL DEFAULT 0,
  supports_best_sellers INTEGER NOT NULL DEFAULT 0,
  is_leaf INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (site, node)
);

CREATE INDEX idx_category_nodes_parent
ON category_nodes(site, parent_node, name);

CREATE INDEX idx_category_nodes_name
ON category_nodes(site, name);

PRAGMA optimize;
