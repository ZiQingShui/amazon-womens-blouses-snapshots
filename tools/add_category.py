from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).parents[1]
PUBLIC_ROOTS = (ROOT / "dist", ROOT / "docs")


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def add_category(node: str, name: str, label: str | None = None) -> dict:
    node = node.strip()
    name = name.strip()
    label = (label or name).strip()
    if not re.fullmatch(r"\d{6,14}", node):
        raise ValueError("类目节点必须是 6–14 位数字")
    if not name:
        raise ValueError("英文类目名称不能为空")

    source = PUBLIC_ROOTS[0] / "data" / "categories.json"
    registry = json.loads(source.read_text(encoding="utf-8"))
    if any(str(item["node"]) == node for item in registry.get("categories", [])):
        raise ValueError(f"类目节点 {node} 已存在")

    category = {
        "site": "US",
        "name": name,
        "label": label,
        "node": node,
        "ranking": "Hot New Releases",
        "url": f"https://www.amazon.com/gp/new-releases/fashion/{node}",
        "manifest": f"data/categories/{node}/manifest.json",
    }
    registry.setdefault("categories", []).append(category)
    waiting = {
        "status": "waiting",
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "categoryNode": node,
        "reason": "等待首次 Top 100 快照",
    }
    manifest = {
        "schemaVersion": 1,
        "categoryNode": node,
        "updatedAt": None,
        "latest": None,
        "snapshots": [],
    }
    for public_root in PUBLIC_ROOTS:
        atomic_json(public_root / "data" / "categories.json", registry)
        category_root = public_root / "data" / "categories" / node
        atomic_json(category_root / "manifest.json", manifest)
        atomic_json(category_root / "status.json", waiting)
    return category


def main() -> None:
    parser = argparse.ArgumentParser(description="Add an Amazon US Hot New Releases category")
    parser.add_argument("--node")
    parser.add_argument("--name", help="English category name")
    parser.add_argument("--label", help="Optional display label")
    args = parser.parse_args()
    node = args.node or input("Amazon category node: ")
    name = args.name or input("English category name: ")
    label = args.label
    category = add_category(node, name, label)
    print(json.dumps(category, ensure_ascii=False, indent=2))
    print("Category added. It will appear as waiting until its first Top 100 snapshot is published.")


if __name__ == "__main__":
    main()
