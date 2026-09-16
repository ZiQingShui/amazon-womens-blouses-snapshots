"""Repair zero-valued display prices using only matching original ranking exports."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from merge_uploaded_ecomtool import RANKING_HEADERS, ranking_table, read_bytes, valid_price
from publish_snapshot import archive_path, category_data_root, has_coverage_value


ROOT = Path(__file__).parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--ranking")
    args = parser.parse_args()
    if (args.date, args.node) not in {
        ("2026-09-15", "2368365011"),
        ("2026-09-16", "2368365011"),
        ("2026-09-16", "2368383011"),
    }:
        raise SystemExit("只修复已核对的 9 月 15、16 日本地快照")
    if (args.date == "2026-09-16") != bool(args.ranking):
        raise SystemExit("9 月 16 日必须提供原始榜单；9 月 15 日原始榜单不在本机")
    export = {}
    export_sha256 = None
    if args.ranking:
        rows, _ = ranking_table(args.ranking)
        if not rows or rows[0] != RANKING_HEADERS:
            raise SystemExit("原始榜单字段不符")
        export = {row[0]: row for row in rows[1:] if len(row) == 10 and row[7] == args.node}
        if len(export) != 100 or {int(row[8]) for row in export.values()} != set(range(1, 101)):
            raise SystemExit("原始榜单 ASIN 或名次不完整")
        export_sha256 = hashlib.sha256(read_bytes(args.ranking)).hexdigest()
    prepared = {}
    changes = []
    for root in (ROOT / "docs", ROOT / "dist"):
        data_root = category_data_root(root, args.node)
        paths = [archive_path(root, args.date, args.node)]
        latest = data_root / "latest.json"
        if latest.exists() and json.loads(latest.read_text(encoding="utf-8")).get("snapshotDate") == args.date:
            paths.append(latest)
        for path in paths:
            if not path.is_file() or ROOT.resolve() not in path.resolve().parents:
                raise SystemExit(f"本地快照路径无效：{path}")
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            items = snapshot.get("items", [])
            if snapshot.get("snapshotDate") != args.date or str(snapshot.get("category", {}).get("node")) != args.node or len(items) != 100:
                raise SystemExit(f"快照日期、节点或商品数不符：{path}")
            if export and (snapshot.get("sources", {}).get("rankingFileSha256") != export_sha256 or {item["asin"] for item in items} != set(export)):
                raise SystemExit(f"原始榜单与快照不匹配：{path}")
            for item in items:
                if item.get("price") != "$0":
                    continue
                row = export.get(item["asin"])
                if row and item["rank"] != int(row[8]):
                    raise SystemExit(f"{item['asin']} 的榜单名次不符")
                replacement = valid_price(row[3]) if row else None
                item["price"] = f"${replacement}" if replacement else "未显示/无法获取"
                item["priceSource"] = "用户上传榜单导出" if replacement else "未显示/无法获取"
                item["priceNote"] = (
                    "商品详情价格 0 非有效报价；采用原始榜单价"
                    if replacement else
                    "商品详情价格 0 非有效报价；原始榜单标记不可售"
                    if row and row[3] == "不可售" else
                    "商品详情价格 0 非有效报价；原始榜单不可核对"
                )
                if root.name == "docs" and path.name != "latest.json":
                    changes.append({"date": args.date, "node": args.node, "rank": item["rank"], "asin": item["asin"], "price": item["price"]})
            snapshot["quality"]["fieldCoverage"]["price"] = sum(has_coverage_value(item, "price") for item in items)
            prepared[path] = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    for path, content in prepared.items():
        path.write_text(content, encoding="utf-8")
    print(json.dumps({"files": len(prepared), "changes": changes}, ensure_ascii=False))


if __name__ == "__main__":
    main()
