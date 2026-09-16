"""Backfill only the matching daily snapshot with feedback from its original ranking export."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from merge_uploaded_ecomtool import RANKING_HEADERS, parse_feedback, ranking_table, read_bytes


ROOT = Path(__file__).parents[1]


def snapshot_paths(node: str, date: str) -> list[Path]:
    suffix = Path("data")
    if node != "2368365011":
        suffix /= Path("categories") / node
    year, month, _ = date.split("-")
    paths = []
    for site_root in (ROOT / "docs", ROOT / "dist"):
        data_root = site_root / suffix
        paths.extend((data_root / "daily" / year / month / f"{date}.json", data_root / "latest.json"))
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ranking", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    if args.node not in {"2368365011", "2368383011"} or args.date != "2026-09-16":
        raise SystemExit("此回填仅允许操作两份 2026-09-16 已核对的本地快照")
    ranking_rows, _ = ranking_table(args.ranking)
    if not ranking_rows or ranking_rows[0] != RANKING_HEADERS:
        raise SystemExit("榜单导出文件字段不符")
    feedback = {}
    ranks = set()
    captured_times = set()
    for row in ranking_rows[1:]:
        if len(row) != 10 or row[7] != args.node or row[0] in feedback:
            raise SystemExit("榜单 ASIN、节点或字段不一致")
        rank = int(row[8])
        ranks.add(rank)
        captured_times.add(row[9])
        feedback[row[0]] = (rank, *parse_feedback(row))
    if ranks != set(range(1, 101)) or len(feedback) != 100 or len(captured_times) != 1:
        raise SystemExit("榜单必须包含 1–100 名、100 个唯一 ASIN 与一个采集时间")
    export_sha256 = hashlib.sha256(read_bytes(args.ranking)).hexdigest()
    captured_at = next(iter(captured_times))
    prepared = {}
    for path in snapshot_paths(args.node, args.date):
        if not path.is_file() or ROOT.resolve() not in path.resolve().parents:
            raise SystemExit(f"本地快照路径无效：{path}")
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        items = snapshot.get("items", [])
        sources = snapshot.get("sources", {})
        if snapshot.get("snapshotDate") != args.date or str(snapshot.get("category", {}).get("node")) != args.node:
            raise SystemExit(f"快照日期或节点不符：{path}")
        if sources.get("rankingFileSha256") != export_sha256 or sources.get("rankingCapturedAt") != captured_at:
            raise SystemExit(f"快照所用榜单与原始文件不符：{path}")
        if len(items) != 100 or {item["asin"] for item in items} != set(feedback):
            raise SystemExit(f"快照 ASIN 不完整或与导出文件不一致：{path}")
        for item in items:
            rank, review_count, rating = feedback[item["asin"]]
            if item["rank"] != rank:
                raise SystemExit(f"商品 {item['asin']} 的快照名次不符")
            item["reviewCount"] = review_count
            item["rating"] = rating
            item["feedbackSource"] = "用户上传榜单导出"
            item["feedbackCapturedAt"] = captured_at
        sources["feedback"] = "用户上传榜单导出"
        sources["feedbackCapturedAt"] = captured_at
        prepared[path] = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    for path, content in prepared.items():
        path.write_text(content, encoding="utf-8")
    print(json.dumps({"node": args.node, "date": args.date, "snapshotFiles": len(prepared), "items": len(feedback), "ratings": sum(value[2] is not None for value in feedback.values()), "reviewCounts": sum(value[1] is not None for value in feedback.values())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
