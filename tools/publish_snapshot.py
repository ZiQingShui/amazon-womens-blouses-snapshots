from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen


ROOT = Path(__file__).parents[1]
PUBLIC_ROOTS = (
    ROOT / "dist",
    ROOT / "docs",
)
DEFAULT_NODE = "2368365011"
CATEGORY_REGISTRY = ROOT / "dist" / "data" / "categories.json"
REMOTE_CATEGORY_REGISTRY = "https://amazon-womens-blouses-snapshots.ziqingshui.chatgpt.site/data/categories.json"
REQUIRED_FIELDS = ("asin", "title", "image")
COVERAGE_FIELDS = (
    "title",
    "image",
    "brand",
    "price",
    "mainBsr",
    "subBsr",
    "listingDate",
    "promotion",
)
MISSING_TEXT = {"", "未显示", "未显示/无法获取", "待补齐", "未知", "unknown", "n/a", "none"}


def load_categories() -> dict[str, dict]:
    registry = json.loads(CATEGORY_REGISTRY.read_text(encoding="utf-8"))
    categories = registry.get("categories", [])
    return {str(category["node"]): category for category in categories}


def load_remote_categories() -> dict[str, dict]:
    try:
        with urlopen(REMOTE_CATEGORY_REGISTRY, timeout=8) as response:
            registry = json.load(response)
    except (OSError, ValueError):
        return {}
    return {str(category["node"]): category for category in registry.get("categories", [])}


def is_allowed_url(value: object, kind: str) -> bool:
    try:
        parsed = urlparse(str(value))
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if kind == "product":
        return host in {"amazon.com", "www.amazon.com"} and "/dp/" in parsed.path
    if host == "ziqingshui.github.io":
        return parsed.path.startswith("/amazon-womens-blouses-snapshots/data/images/")
    return (
        host == "images-na.ssl-images-amazon.com"
        or host.endswith(".media-amazon.com")
        or host.endswith(".ssl-images-amazon.com")
    )


def read_input(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8-sig").strip()
    if path.suffix.lower() == ".js":
        raw = raw.split("=", 1)[1].strip().rstrip(";")
    payload = json.loads(raw)
    if isinstance(payload, dict):
        payload = payload.get("items", payload.get("new_releases"))
    if not isinstance(payload, list):
        raise ValueError("输入文件必须包含商品数组")
    return payload


def clean_item(item: dict, snapshot_date: str) -> dict:
    row = dict(item)
    row["rank"] = int(row["rank"])
    row["asin"] = str(row.get("asin", "")).strip().upper()
    row["sourceDate"] = snapshot_date
    promotions = [str(value).strip() for value in row.get("promotions", []) if str(value).strip()]
    if promotions:
        row["promotions"] = list(dict.fromkeys(promotions))
        row["promotion"] = " + ".join(row["promotions"])
        row["promotionStatus"] = "detected"
    elif row.get("promotionStatus") == "none" or row.get("promotion") in {"未检测到", "否", ""}:
        row["promotion"] = "暂无促销"
        row["promotionStatus"] = "none"
    elif row.get("promotion"):
        row["promotionStatus"] = row.get("promotionStatus") or "detected"
    else:
        row["promotion"] = "未显示/无法获取"
        row["promotionStatus"] = "unknown"
    if not row.get("url") and re.fullmatch(r"[A-Z0-9]{10}", row["asin"]):
        row["url"] = f"https://www.amazon.com/dp/{row['asin']}"
    row.pop("error", None)
    return row


def has_coverage_value(row: dict, field: str) -> bool:
    value = row.get(field)
    if field in {"mainBsr", "subBsr"}:
        try:
            return int(value) > 0
        except (TypeError, ValueError):
            return False
    if field == "listingDate":
        try:
            date.fromisoformat(str(value))
            return True
        except (TypeError, ValueError):
            return False
    if field == "promotion":
        return row.get("promotionStatus") in {"detected", "none"}
    return str(value or "").strip().lower() not in MISSING_TEXT


def validate(items: list[dict]) -> dict:
    ranks = [row.get("rank") for row in items]
    asins = [row.get("asin") for row in items]
    duplicate_ranks = sorted({rank for rank in ranks if ranks.count(rank) > 1})
    duplicate_asins = sorted({asin for asin in asins if asin and asins.count(asin) > 1})
    missing_ranks = sorted(set(range(1, 101)) - set(ranks))
    required_missing = {
        field: sum(not bool(row.get(field)) for row in items) for field in REQUIRED_FIELDS
    }
    invalid_urls = {
        "product": sum(not is_allowed_url(row.get("url"), "product") for row in items),
        "image": sum(not is_allowed_url(row.get("image"), "image") for row in items),
    }
    coverage = {
        field: sum(has_coverage_value(row, field) for row in items) for field in COVERAGE_FIELDS
    }
    publishable = (
        len(items) == 100
        and not missing_ranks
        and not duplicate_ranks
        and not duplicate_asins
        and not any(required_missing.values())
        and not any(invalid_urls.values())
    )
    return {
        "count": len(items),
        "publishable": publishable,
        "missingRanks": missing_ranks,
        "duplicateRanks": duplicate_ranks,
        "duplicateAsins": duplicate_asins,
        "requiredMissing": required_missing,
        "invalidUrls": invalid_urls,
        "fieldCoverage": coverage,
    }


def category_data_root(public_root: Path, node: str = DEFAULT_NODE) -> Path:
    if node == DEFAULT_NODE:
        return public_root / "data"
    return public_root / "data" / "categories" / node


def archive_path(public_root: Path, date: str, node: str = DEFAULT_NODE) -> Path:
    year, month, _ = date.split("-")
    return category_data_root(public_root, node) / "daily" / year / month / f"{date}.json"


def read_existing_snapshots(public_root: Path, before_date: str, node: str = DEFAULT_NODE) -> list[dict]:
    daily_root = category_data_root(public_root, node) / "daily"
    snapshots = []
    if not daily_root.exists():
        return snapshots
    for path in daily_root.glob("*/*/*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("snapshotDate", "") < before_date:
            snapshots.append(payload)
    return sorted(snapshots, key=lambda row: row["snapshotDate"])


def add_history(items: list[dict], earlier: list[dict], snapshot_date: str) -> None:
    appearances: dict[str, int] = {}
    first_seen: dict[str, str] = {}
    last_seen: set[str] = set()
    streaks: dict[str, int] = {}
    previous_date: date | None = None
    for snapshot in earlier:
        current_date = date.fromisoformat(snapshot["snapshotDate"])
        follows_previous_day = previous_date is not None and (current_date - previous_date).days == 1
        current = {row["asin"] for row in snapshot.get("items", [])}
        for asin in current:
            appearances[asin] = appearances.get(asin, 0) + 1
            first_seen.setdefault(asin, snapshot["snapshotDate"])
            streaks[asin] = streaks.get(asin, 0) + 1 if follows_previous_day and asin in last_seen else 1
        for asin in set(streaks) - current:
            streaks[asin] = 0
        last_seen = current
        previous_date = current_date
    current_date = date.fromisoformat(snapshot_date)
    follows_previous_day = previous_date is not None and (current_date - previous_date).days == 1
    for row in items:
        asin = row["asin"]
        row["history"] = {
            "firstSeen": first_seen.get(asin, snapshot_date),
            "appearances": appearances.get(asin, 0) + 1,
            "streak": streaks.get(asin, 0) + 1 if follows_previous_day and asin in last_seen else 1,
        }


def persist_remote_category(category: dict) -> None:
    node = str(category["node"])
    for public_root in PUBLIC_ROOTS:
        path = public_root / "data" / "categories.json"
        registry = json.loads(path.read_text(encoding="utf-8"))
        rows = registry.setdefault("categories", [])
        if not any(str(row.get("node")) == node for row in rows):
            rows.append(category)
            atomic_json(path, registry)


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def rebuild_manifest(public_root: Path, node: str = DEFAULT_NODE) -> dict:
    entries = []
    data_root = category_data_root(public_root, node)
    for path in (data_root / "daily").glob("*/*/*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries.append({
            "date": payload["snapshotDate"],
            "capturedAt": payload["capturedAt"],
            "file": path.relative_to(public_root).as_posix(),
            "count": payload["quality"]["count"],
            "publishable": payload["quality"]["publishable"],
            "fieldCoverage": payload["quality"]["fieldCoverage"],
        })
    entries.sort(key=lambda row: row["date"], reverse=True)
    manifest = {
        "schemaVersion": 1,
        "categoryNode": node,
        "updatedAt": entries[0]["capturedAt"] if entries else None,
        "latest": entries[0]["date"] if entries else None,
        "snapshots": entries,
    }
    atomic_json(data_root / "manifest.json", manifest)
    return manifest


def publish(input_path: Path, snapshot_date: str, captured_at: str, detail_source: str, node: str = DEFAULT_NODE, replace_current_day: bool = False) -> dict:
    categories = load_categories()
    if node not in categories:
        remote_categories = load_remote_categories()
        categories.update(remote_categories)
        if node in remote_categories:
            persist_remote_category(remote_categories[node])
    if node not in categories:
        raise SystemExit(f"未配置的类目节点：{node}")
    category = categories[node]
    data_roots = [category_data_root(public_root, node) for public_root in PUBLIC_ROOTS]
    items = sorted((clean_item(row, snapshot_date) for row in read_input(input_path)), key=lambda row: row["rank"])
    quality = validate(items)
    if not quality["publishable"]:
        failure = {
            "status": "failed",
            "attemptedAt": captured_at,
            "snapshotDate": snapshot_date,
            "reason": "快照未通过 Top 100 完整性校验",
            "quality": quality,
        }
        for data_root in data_roots:
            atomic_json(data_root / "status.json", failure)
        raise SystemExit(json.dumps(failure, ensure_ascii=False))

    existing_archives = [
        archive_path(public_root, snapshot_date, node)
        for public_root in PUBLIC_ROOTS
        if archive_path(public_root, snapshot_date, node).exists()
    ]
    today_beijing = datetime.now().astimezone().strftime("%Y-%m-%d")
    if replace_current_day and snapshot_date != today_beijing:
        raise SystemExit("--replace-current-day 只能用于当天快照")
    if existing_archives and not replace_current_day:
        failure = {
            "status": "failed",
            "attemptedAt": captured_at,
            "snapshotDate": snapshot_date,
            "reason": "当天快照已存在；历史快照禁止覆盖",
        }
        for data_root in data_roots:
            atomic_json(data_root / "status.json", failure)
        raise SystemExit(json.dumps(failure, ensure_ascii=False))

    earlier = read_existing_snapshots(PUBLIC_ROOTS[0], snapshot_date, node)
    add_history(items, earlier, snapshot_date)
    snapshot = {
        "schemaVersion": 1,
        "snapshotDate": snapshot_date,
        "capturedAt": captured_at,
        "category": category,
        "sources": {"ranking": "Amazon 官方新品榜", "productDetails": detail_source},
        "quality": quality,
        "items": items,
    }
    for public_root in PUBLIC_ROOTS:
        data_root = category_data_root(public_root, node)
        destination = archive_path(public_root, snapshot_date, node)
        atomic_json(destination, snapshot)
        atomic_json(data_root / "latest.json", snapshot)
        manifest = rebuild_manifest(public_root, node)
        atomic_json(data_root / "status.json", {
            "status": "ok",
            "updatedAt": captured_at,
            "snapshotDate": snapshot_date,
            "count": len(items),
            "manifestSnapshots": len(manifest["snapshots"]),
        })
    return {"date": snapshot_date, "node": node, "items": len(items), "quality": quality}


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and publish one Amazon Top 100 snapshot")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--date", required=True)
    parser.add_argument("--captured-at", default=None)
    parser.add_argument("--node", default=DEFAULT_NODE)
    parser.add_argument("--detail-source", default="Ecomtool MCP + Amazon 商品详情页")
    parser.add_argument("--replace-current-day", action="store_true", help="仅允许替换今天的快照，用于手动修复或刷新")
    args = parser.parse_args()
    datetime.strptime(args.date, "%Y-%m-%d")
    captured_at = args.captured_at or f"{args.date}T08:30:00+08:00"
    result = publish(args.input.resolve(), args.date, captured_at, args.detail_source, args.node, args.replace_current_day)
    # Sites serves data embedded in the Worker bundle, so every successful
    # snapshot publication must refresh that bundle before deployment.
    from build_worker import main as build_worker_main

    build_worker_main()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
