"""把市场调研数据（父ASIN + 上架日期）补进已发布的快照 items。

- 给每个 item 加 parentAsin 字段
- 用市场调研的 listingDate 覆盖「未显示/无法获取」（仅当市场调研有值）
- 同步 docs 和 dist，重建 manifest 和 Worker

用法：
  python tools/backfill_parent_listing.py --date 2026-09-18
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
WORK = ROOT / "work"
sys.path.insert(0, str(ROOT / "tools"))
import publish_snapshot  # noqa: E402


def snapshot_paths(date: str) -> list[tuple[str, str]]:
    """返回 (node, ranking) 的 4 组快照。"""
    return [
        ("2368365011", "new-releases"),
        ("2368383011", "new-releases"),
        ("2368365011", "best-sellers"),
        ("2368383011", "best-sellers"),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    date = args.date
    market = json.loads((WORK / f"market-analysis-{date}.json").read_text(encoding="utf-8"))

    for node, ranking in snapshot_paths(date):
        data_root = publish_snapshot.category_data_root(Path("docs"), node, ranking)
        archive = publish_snapshot.archive_path(Path("docs"), date, node, ranking)
        if not archive.exists():
            print(f"跳过 {ranking}/{node}：快照不存在")
            continue
        snapshot = json.loads(archive.read_text(encoding="utf-8"))
        added_parent = 0
        added_date = 0
        for item in snapshot["items"]:
            asin = item["asin"]
            info = market.get(asin)
            if not info:
                continue
            if info.get("parentAsin"):
                item["parentAsin"] = str(info["parentAsin"])
                item["variantCount"] = info.get("variantCount")
                added_parent += 1
            ld = info.get("listingDate")
            if ld and str(item.get("listingDate", "")).strip().lower() in ("", "未显示/无法获取"):
                item["listingDate"] = ld
                item["listingDateSource"] = "Ecomtool MCP 市场调研上架日期；非 Amazon Date First Available 核验"
                added_date += 1

        # 重算 quality 并写 docs + dist
        detail_source = (snapshot.get("sources") or {}).get("productDetails")
        snapshot["quality"] = publish_snapshot.validate(snapshot["items"], detail_source)
        for root in ["docs", "dist"]:
            target = publish_snapshot.archive_path(Path(root), date, node, ranking)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            latest = publish_snapshot.category_data_root(Path(root), node, ranking) / "latest.json"
            latest.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        fc = snapshot["quality"]["fieldCoverage"]
        print(f"{ranking}/{node}: 补父ASIN {added_parent}, 补上架日期 {added_date} -> listing 覆盖 {fc['listingDate']}/100")

    # 重建 manifest + Worker
    for root in ["docs", "dist"]:
        for node, ranking in snapshot_paths(date):
            publish_snapshot.rebuild_manifest(Path(root), node, ranking)
    from build_worker import main as build_worker_main
    build_worker_main()
    print("\n完成：manifest 已重建，Worker 已刷新")


if __name__ == "__main__":
    main()
