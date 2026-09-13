from __future__ import annotations

import json
import mimetypes
from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "docs"
OUTPUT = ROOT / "dist" / "server" / "index.js"


def assets() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for path in SOURCE.rglob("*"):
        if not path.is_file():
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
  return {
    site: "US",
    name: row.name,
    label: row.label || row.name,
    node: row.node,
    ranking: "Hot New Releases",
    url: `https://www.amazon.com/gp/new-releases/fashion/${row.node}`,
    manifest: `data/categories/${row.node}/manifest.json`
  };
}

async function customCategories(env) {
  if (!env.DB) return [];
  const result = await env.DB.prepare("SELECT node, name, label, created_at FROM categories ORDER BY created_at, node").all();
  return (result.results || []).map(categoryShape);
}

async function registry(env) {
  const custom = await customCategories(env);
  const known = new Set(BASE_REGISTRY.categories.map(item => String(item.node)));
  return {...BASE_REGISTRY, categories: [...BASE_REGISTRY.categories, ...custom.filter(item => !known.has(String(item.node)))]};
}

async function categoryTree(env, url) {
  if (!env.DB) return reply({nodes: []});
  const query = String(url.searchParams.get("q") || "").trim();
  if (query) {
    const result = await env.DB.prepare("SELECT site, node, name, parent_node, depth, path, department_slug, supports_new_releases, supports_best_sellers, is_leaf FROM category_nodes WHERE site = 'US' AND (node = ? OR name LIKE ?) ORDER BY depth, name LIMIT 100").bind(query, `%${query}%`).all();
    return reply({nodes: result.results || []});
  }
  const parent = url.searchParams.get("parent");
  const statement = parent
    ? env.DB.prepare("SELECT site, node, name, parent_node, depth, path, department_slug, supports_new_releases, supports_best_sellers, is_leaf FROM category_nodes WHERE site = 'US' AND parent_node = ? ORDER BY name LIMIT 500").bind(parent)
    : env.DB.prepare("SELECT site, node, name, parent_node, depth, path, department_slug, supports_new_releases, supports_best_sellers, is_leaf FROM category_nodes WHERE site = 'US' AND parent_node IS NULL ORDER BY name LIMIT 500");
  const result = await statement.all();
  return reply({nodes: result.results || []});
}

function isOwner(request, env) {
  const userId = request.headers.get("oai-authenticated-user-id");
  return Boolean(env.OWNER_USER_ID && userId && userId === env.OWNER_USER_ID);
}

async function addCategory(request, env, url) {
  const origin = request.headers.get("origin");
  if ((origin && origin !== url.origin) || request.headers.get("sec-fetch-site") === "cross-site") {
    return reply({error: "请求来源无效"}, 403);
  }
  if (!isOwner(request, env)) {
    return reply({error: "需要管理员使用 ChatGPT 登录", signIn: "/signin-with-chatgpt?return_to=/"}, 401);
  }
  let body;
  try { body = await request.json(); } catch { return reply({error: "请输入有效的类目信息"}, 400); }
  const node = String(body.node || "").trim();
  const name = String(body.name || "").trim() || `未命名类目 ${node}`;
  const label = String(body.label || "").trim() || name;
  if (!/^\d{6,14}$/.test(node)) return reply({error: "类目节点必须是 6–14 位数字"}, 400);
  if (name.length > 120 || label.length > 120) return reply({error: "类目名称不能超过 120 个字符"}, 400);
  if (BASE_REGISTRY.categories.some(item => String(item.node) === node)) return reply({error: "该类目已经存在"}, 409);
  try {
    await env.DB.prepare("INSERT INTO categories (node, name, label) VALUES (?, ?, ?)").bind(node, name, label).run();
  } catch (error) {
    if (String(error).toLowerCase().includes("unique")) return reply({error: "该类目已经存在"}, 409);
    return reply({error: "类目保存失败，请稍后重试"}, 500);
  }
  return reply({category: categoryShape({node, name, label}), status: "waiting"}, 201);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/categories" && request.method === "POST") return addCategory(request, env, url);
    if (url.pathname === "/api/category-tree" && request.method === "GET") {
      try { return await categoryTree(env, url); }
      catch { return reply({nodes: []}); }
    }
    if (url.pathname === "/data/categories.json" && request.method === "GET") {
      try { return reply(await registry(env)); }
      catch { return reply(BASE_REGISTRY); }
    }
    const manifestMatch = url.pathname.match(/^\/data\/categories\/(\d{6,14})\/manifest\.json$/);
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
