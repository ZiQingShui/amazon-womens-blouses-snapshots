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


def add_category(
    node: str,
    name: str,
    label: str | None = None,
    path: list[str] | None = None,
    department_slug: str = "fashion",
) -> dict:
    node = node.strip()
    name = name.strip()
    label = (label or name).strip()
    if not re.fullmatch(r"\d{6,14}", node):
        raise ValueError("类目节点必须是 6–14 位数字")
    if not name:
        raise ValueError("英文类目名称不能为空")
    # 与看板 /api/categories 写入的字段保持一致，否则前端拿不到层级路径，
    # 仓库自带测试 test_seed_categories_include_hierarchical_paths 也会失败。
    parts = [str(part).strip() for part in (path or []) if str(part).strip()][:12]
    if any(len(part) > 120 for part in parts):
        raise ValueError("类目路径单段不能超过 120 个字符")
    parts = parts or [name]
    department_slug = (department_slug or "fashion").strip().lower()
    if not re.fullmatch(r"[a-z0-9-]{2,60}", department_slug):
        raise ValueError("Amazon 类目标识必须是 2–60 位小写字母、数字或连字符")

    source = PUBLIC_ROOTS[0] / "data" / "categories.json"
    registry = json.loads(source.read_text(encoding="utf-8"))
    if any(str(item["node"]) == node for item in registry.get("categories", [])):
        raise ValueError(f"类目节点 {node} 已存在")

    category = {
        "site": "US",
        "name": name,
        "label": label,
        "node": node,
        "path": parts,
        "departmentSlug": department_slug,
        "ranking": "Hot New Releases",
        "url": f"https://www.amazon.com/gp/new-releases/{department_slug}/{node}",
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
    parser.add_argument("--path", help="Optional hierarchy path, e.g. 'Clothing, Shoes & Jewelry/Women/Clothing'")
    parser.add_argument("--department-slug", default="fashion", help="Amazon department slug, default fashion")
    args = parser.parse_args()
    node = args.node or input("Amazon category node: ")
    name = args.name or input("English category name: ")
    label = args.label
    path = [part for part in (args.path or "").split("/") if part.strip()]
    category = add_category(node, name, label, path, args.department_slug)
    print(json.dumps(category, ensure_ascii=False, indent=2))
    print("Category added. It will appear as waiting until its first Top 100 snapshot is published.")
    # 站点侧 /data/categories.json 是由 Worker 内嵌的 BASE_REGISTRY 提供的，
    # 不重建 bundle 的话，新类目在 Sites 部署上要等到下一次快照发布才可见。
    from build_worker import main as build_worker_main

    build_worker_main()
    print("Worker bundle rebuilt so the Sites deployment serves the new category.")


if __name__ == "__main__":
    main()
