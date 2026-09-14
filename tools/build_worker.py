from __future__ import annotations

import json
import mimetypes
from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "docs"
OUTPUT = ROOT / "dist" / "server" / "index.js"
LEGACY_SCRIPT_PREFIX = "amazon_womens_blouses_"


def assets() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for path in SOURCE.rglob("*"):
        if not path.is_file():
            continue
        try:
            path.relative_to(SOURCE / "data" / "images")
            is_product_image = True
        except ValueError:
            is_product_image = False
        if is_product_image:
            # Product images are served by GitHub Pages using their canonical
            # public URLs; embedding binary assets would bloat the Worker.
            continue
        if path.parent == SOURCE and path.name.startswith(LEGACY_SCRIPT_PREFIX) and path.suffix == ".js":
            continue
        route = "/" + path.relative_to(SOURCE).as_posix()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/json", "application/javascript"}:
            content_type += "; charset=utf-8"
        result[route] = {
            "contentType": content_type,
            "body": path.read_text(encoding="utf-8"),
        }
    return result


WORKER = r'''
const STATIC_ASSETS = __ASSETS__;
const BASE_REGISTRY = JSON.parse(STATIC_ASSETS["/data/categories.json"].body);
const JSON_HEADERS = {"content-type":"application/json; charset=utf-8","cache-control":"no-store"};

function reply(payload, status = 200) {
  return new Response(JSON.stringify(payload), {status, headers: JSON_HEADERS});
}

function categoryShape(row) {
  let path = row.path;
  if (typeof path === "string") {
    try { path = JSON.parse(path); } catch { path = []; }
  }
  const departmentSlug = row.department_slug || row.departmentSlug || "fashion";
  return {
    site: "US",
    name: row.name,
    label: row.label || row.name,
    node: row.node,
    path: Array.isArray(path) && path.length ? path : [row.name],
    departmentSlug,
    ranking: "Hot New Releases",
    url: `https://www.amazon.com/gp/new-releases/${departmentSlug}/${row.node}`,
    manifest: `data/categories/${row.node}/manifest.json`,
    deletable: true
  };
}

function categoryNodeShape(row) {
  let path = row.path;
  if (typeof path === "string") {
    try { path = JSON.parse(path); } catch { path = []; }
  }
  const rawNode = String(row.node || "");
  return {
    name: row.name,
    node: rawNode.startsWith("slug:") ? null : rawNode,
    slug: row.department_slug || row.departmentSlug || "fashion",
    path: Array.isArray(path) && path.length ? path : [row.name],
    supportsNewReleases: Boolean(row.supports_new_releases ?? row.supportsNewReleases),
    supportsBestSellers: Boolean(row.supports_best_sellers ?? row.supportsBestSellers),
    isLeaf: Boolean(row.is_leaf ?? row.isLeaf)
  };
}

function decodeHtml(value) {
  const named = {amp: "&", quot: '"', apos: "'", lt: "<", gt: ">", nbsp: " "};
  return String(value || "")
    .replace(/<[^>]*>/g, " ")
    .replace(/&#(x?[0-9a-f]+);/gi, (_, raw) => String.fromCodePoint(parseInt(raw.replace(/^x/i, ""), /^x/i.test(raw) ? 16 : 10)))
    .replace(/&([a-z]+);/gi, (match, name) => named[name.toLowerCase()] || match)
    .replace(/\s+/g, " ")
    .trim();
}

async function remoteBrowseChildren(parentNode, parentPath, departmentSlug) {
  if (!/^\d{1,14}$/.test(parentNode)) return [];
  const departmentPath = String(parentPath[0] || "").toLowerCase().replace(/&/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const winningCatUrl = parentPath.length === 1
    ? `https://winningcat.com/departments/${departmentPath}`
    : `https://winningcat.com/nodes/${parentNode}`;
  const requestOptions = {headers: {accept: "text/html", "user-agent": "Mozilla/5.0 (compatible; BSRRadar/1.0)"}, cf: {cacheEverything: true, cacheTtl: 86400}};
  let response = await fetch(winningCatUrl, requestOptions), html = response.ok ? await response.text() : "", section = "", legacy = false;
  if (html) {
    const lower = html.toLowerCase(), start = lower.indexOf("sub-categories"), other = lower.indexOf("other categories", Math.max(0, start)), end = other >= 0 ? other : lower.indexOf("need all", Math.max(0, start));
    section = start >= 0 ? html.slice(start, end > start ? end : html.length) : "";
  }
  if (!section) {
    const legacyUrl = `https://www.browsenodes.com/amazon.com/browseNodeLookup/${parentNode}.html`;
    response = await fetch(legacyUrl, requestOptions);
    if (!response.ok) throw new Error(`category source returned ${response.status}`);
    html = await response.text();
    const lower = html.toLowerCase(), marker = lower.indexOf("also has"), tableStart = lower.indexOf("<table", Math.max(0, marker)), tableEnd = tableStart >= 0 ? lower.indexOf("</table>", tableStart) : -1;
    if (tableStart < 0 || tableEnd < 0) return [];
    section = html.slice(tableStart, tableEnd + 8);
    legacy = true;
  }
  const rows = [], seen = new Set();
  const candidates = legacy
    ? [...section.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi)].map(match => {const row = match[1], nodeMatch = row.match(/\/amazon\.com\/browseNodeLookup\/(\d+)\.html/i), cells = [...row.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/gi)];return nodeMatch ? [nodeMatch[1], cells[1]?.[1] || ""] : null}).filter(Boolean)
    : [...section.matchAll(/<a[^>]+href=["'][^"']*\/nodes\/(\d+)[^"']*["'][^>]*>([\s\S]*?)<\/a>/gi)].map(match => [match[1], match[2]]);
  for (const candidate of candidates) {
    const node = candidate[0];
    let name = decodeHtml(candidate[1]);
    name = name.replace(new RegExp(`\\s+${node}(?:\\s+[\\d,]+\\s+nodes?)?$`, "i"), "").trim();
    if (!name || node === parentNode || seen.has(node)) continue;
    seen.add(node);
    rows.push({
      name,
      node,
      slug: departmentSlug || "fashion",
      path: [...parentPath, name],
      supportsNewReleases: true,
      supportsBestSellers: true,
      isLeaf: false
    });
  }
  return rows;
}

async function customCategories(env) {
  if (!env.DB) return [];
  const result = await env.DB.prepare("SELECT node, name, label, path, department_slug, created_at FROM categories ORDER BY created_at, node").all();
  return (result.results || []).map(categoryShape);
}

async function registry(env) {
  const custom = await customCategories(env);
  const known = new Set(BASE_REGISTRY.categories.map(item => String(item.node)));
  return {...BASE_REGISTRY, categories: [...BASE_REGISTRY.categories, ...custom.filter(item => !known.has(String(item.node)))]};
}

async function categoryTree(env, url) {
  if (url.searchParams.get("all") === "1") {
    if (!env.DB) return reply({nodes: []});
    const result = await env.DB.prepare("SELECT site, node, name, parent_node, depth, path, department_slug, supports_new_releases, supports_best_sellers, is_leaf FROM category_nodes WHERE site = 'US' ORDER BY depth, name LIMIT 5000").all();
    return reply({nodes: (result.results || []).map(categoryNodeShape)});
  }
  const query = String(url.searchParams.get("q") || "").trim();
  if (query) {
    if (!env.DB) return reply({nodes: []});
    const result = await env.DB.prepare("SELECT site, node, name, parent_node, depth, path, department_slug, supports_new_releases, supports_best_sellers, is_leaf FROM category_nodes WHERE site = 'US' AND (node = ? OR name LIKE ?) ORDER BY depth, name LIMIT 100").bind(query, `%${query}%`).all();
    return reply({nodes: (result.results || []).map(categoryNodeShape)});
  }
  const parent = url.searchParams.get("parent");
  if (!parent) {
    if (!env.DB) return reply({nodes: []});
    const result = await env.DB.prepare("SELECT site, node, name, parent_node, depth, path, department_slug, supports_new_releases, supports_best_sellers, is_leaf FROM category_nodes WHERE site = 'US' AND parent_node IS NULL ORDER BY name LIMIT 500").all();
    return reply({nodes: (result.results || []).map(categoryNodeShape), source: "database"});
  }
  if (env.DB) {
    const result = await env.DB.prepare("SELECT site, node, name, parent_node, depth, path, department_slug, supports_new_releases, supports_best_sellers, is_leaf FROM category_nodes WHERE site = 'US' AND parent_node = ? ORDER BY name LIMIT 500").bind(parent).all();
    const nodes = (result.results || []).map(categoryNodeShape);
    if (nodes.length) return reply({nodes, source: "database"});
  }
  let parentPath = [];
  try { parentPath = JSON.parse(url.searchParams.get("path") || "[]"); } catch { parentPath = []; }
  if (!Array.isArray(parentPath) || parentPath.some(part => typeof part !== "string")) parentPath = [];
  const slug = String(url.searchParams.get("slug") || "fashion").replace(/[^a-z0-9-]/g, "").slice(0, 60) || "fashion";
  try {
    const nodes = await remoteBrowseChildren(String(parent), parentPath.slice(0, 12), slug);
    return reply({nodes, source: "live"});
  } catch {
    return reply({nodes: [], source: "unavailable"}, 502);
  }
}

async function addCategory(request, env, url) {
  const origin = request.headers.get("origin");
  if ((origin && origin !== url.origin) || request.headers.get("sec-fetch-site") === "cross-site") {
    return reply({error: "请求来源无效"}, 403);
  }
  let body;
  try { body = await request.json(); } catch { return reply({error: "请输入有效的类目信息"}, 400); }
  const node = String(body.node || "").trim();
  const name = String(body.name || "").trim() || `未命名类目 ${node}`;
  const label = String(body.label || "").trim() || name;
  const path = Array.isArray(body.path) ? body.path.map(part => String(part).trim()).filter(Boolean).slice(0, 12) : [];
  const departmentSlug = String(body.departmentSlug || "fashion").trim().toLowerCase();
  if (!/^\d{1,14}$/.test(node)) return reply({error: "类目节点必须是 1–14 位数字"}, 400);
  if (name.length > 120 || label.length > 120) return reply({error: "类目名称不能超过 120 个字符"}, 400);
  if (!/^[a-z0-9-]{2,60}$/.test(departmentSlug)) return reply({error: "Amazon 类目标识无效"}, 400);
  if (path.some(part => part.length > 120)) return reply({error: "类目路径内容过长"}, 400);
  if (BASE_REGISTRY.categories.some(item => String(item.node) === node)) return reply({error: "该类目已经存在"}, 409);
  try {
    await env.DB.prepare("INSERT INTO categories (node, name, label, path, department_slug) VALUES (?, ?, ?, ?, ?)").bind(node, name, label, JSON.stringify(path.length ? path : [name]), departmentSlug).run();
  } catch (error) {
    if (String(error).toLowerCase().includes("unique")) return reply({error: "该类目已经存在"}, 409);
    return reply({error: "类目保存失败，请稍后重试"}, 500);
  }
  return reply({category: categoryShape({node, name, label, path, departmentSlug}), status: "waiting"}, 201);
}

async function deleteCategory(request, env, url, node) {
  const origin = request.headers.get("origin");
  if ((origin && origin !== url.origin) || request.headers.get("sec-fetch-site") === "cross-site") {
    return reply({error: "请求来源无效"}, 403);
  }
  if (!/^\d{1,14}$/.test(node)) return reply({error: "类目节点无效"}, 400);
  if (BASE_REGISTRY.categories.some(item => String(item.node) === node)) {
    return reply({error: "系统内置类目不能删除"}, 403);
  }
  if (!env.DB) return reply({error: "类目服务暂时不可用"}, 503);
  try {
    const existing = await env.DB.prepare("SELECT node FROM categories WHERE node = ? LIMIT 1").bind(node).first();
    if (!existing) return reply({error: "该类目不存在或已被删除"}, 404);
    await env.DB.prepare("DELETE FROM categories WHERE node = ?").bind(node).run();
    return reply({deleted: true, node});
  } catch {
    return reply({error: "类目删除失败，请稍后重试"}, 500);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/categories" && request.method === "POST") return addCategory(request, env, url);
    const categoryDeleteMatch = url.pathname.match(/^\/api\/categories\/(\d{1,14})$/);
    if (categoryDeleteMatch && request.method === "DELETE") return deleteCategory(request, env, url, categoryDeleteMatch[1]);
    if (url.pathname === "/api/category-tree" && request.method === "GET") {
      try { return await categoryTree(env, url); }
      catch { return reply({nodes: []}); }
    }
    if (url.pathname === "/data/categories.json" && request.method === "GET") {
      try { return reply(await registry(env)); }
      catch { return reply(BASE_REGISTRY); }
    }
    const manifestMatch = url.pathname.match(/^\/data\/categories\/(\d{1,14})\/manifest\.json$/);
    if (manifestMatch && request.method === "GET" && !STATIC_ASSETS[url.pathname]) {
      const rows = await customCategories(env);
      if (rows.some(item => item.node === manifestMatch[1])) {
        return reply({schemaVersion: 1, categoryNode: manifestMatch[1], updatedAt: null, latest: null, snapshots: []});
      }
    }
    if (request.method !== "GET" && request.method !== "HEAD") return new Response("Method Not Allowed", {status: 405});
    const path = url.pathname === "/" ? "/index.html" : url.pathname;
    const asset = STATIC_ASSETS[path];
    if (!asset) return new Response("Not Found", {status: 404});
    return new Response(request.method === "HEAD" ? null : asset.body, {
      headers: {"content-type": asset.contentType, "cache-control": path === "/index.html" ? "no-cache" : "public, max-age=60"}
    });
  }
};
'''


def main() -> None:
    payload = WORKER.replace("__ASSETS__", json.dumps(assets(), ensure_ascii=False, separators=(",", ":")))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(payload, encoding="utf-8")
    built_config = ROOT / "dist" / ".openai" / "hosting.json"
    built_config.parent.mkdir(parents=True, exist_ok=True)
    built_config.write_text((ROOT / ".openai" / "hosting.json").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Built {OUTPUT.relative_to(ROOT)} with {len(assets())} assets")


if __name__ == "__main__":
    main()
