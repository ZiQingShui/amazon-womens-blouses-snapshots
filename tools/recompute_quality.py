"""按当前校验口径回算已归档快照的 quality 派生指标。

背景：`validate()` 的口径一旦变化，旧存档里写死的 `quality` 就会与事实脱节。
2026-09-13 那期就是如此——覆盖率算法在当天 19:38 收紧后，10:31 发布的快照
至今仍标注 8 个字段全 100%，而实际 listingDate 只有 84/100。

本脚本只重算派生指标（quality）并同步 latest.json 与 manifest.json，
**不会修改任何商品数据（items）**，因此不违反「历史快照禁止覆盖」的约束。

    python tools/recompute_quality.py --dry-run   # 只看差异
    python tools/recompute_quality.py             # 写入 dist 与 docs
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:  # 作为包被导入（tests: from tools import recompute_quality）
    from tools import publish_snapshot as ps
except ImportError:  # 直接以脚本运行（python tools/recompute_quality.py）
    import publish_snapshot as ps


def category_nodes() -> list[str]:
    registry = json.loads(ps.CATEGORY_REGISTRY.read_text(encoding="utf-8"))
    nodes = [str(item["node"]) for item in registry.get("categories", [])]
    return nodes or [ps.DEFAULT_NODE]


def recompute_node(public_root: Path, node: str, dry_run: bool = False) -> dict:
    data_root = ps.category_data_root(public_root, node)
    daily_root = data_root / "daily"
    report = {"node": node, "checked": 0, "updated": [], "latestUpdated": False, "manifestUpdated": False}
    if not daily_root.exists():
        return report

    snapshots: dict[str, tuple[dict, Path]] = {}
    for path in sorted(daily_root.glob("*/*/*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        archive_date = payload.get("snapshotDate")
        if not archive_date:
            continue
        report["checked"] += 1
        fresh = ps.validate(payload.get("items", []))
        # 无论是否写入，都先按当前口径规整成归一化副本，这样 dry-run 的
        # latest / manifest 差异判断才和真实写入一致。
        normalized = dict(payload, quality=fresh)
        if payload.get("quality") != fresh:
            report["updated"].append(archive_date)
            if not dry_run:
                ps.atomic_json(path, normalized)
        snapshots[archive_date] = (normalized, path)

    if not snapshots:
        return report

    latest_date = max(snapshots)
    latest_payload, _ = snapshots[latest_date]
    latest_path = data_root / "latest.json"
    current_latest = json.loads(latest_path.read_text(encoding="utf-8")) if latest_path.exists() else None
    if current_latest != latest_payload:
        report["latestUpdated"] = True
        if not dry_run:
            ps.atomic_json(latest_path, latest_payload)

    manifest_path = data_root / "manifest.json"
    current_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    fresh_manifest = ps.build_manifest(public_root, node)
    report["manifestUpdated"] = current_manifest != fresh_manifest
    if not dry_run:
        ps.rebuild_manifest(public_root, node)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Recompute archived snapshot quality with the current validator")
    parser.add_argument("--dry-run", action="store_true", help="只报告差异，不写入任何文件")
    args = parser.parse_args()

    for public_root in ps.PUBLIC_ROOTS:
        for node in category_nodes():
            report = recompute_node(public_root, node, args.dry_run)
            if not report["checked"]:
                continue
            updated = f"（{', '.join(report['updated'])}）" if report["updated"] else ""
            manifest_state = "需重建" if report["manifestUpdated"] else "一致"
            print(
                f"[{public_root.name}] 节点 {report['node']}：检查 {report['checked']} 期，"
                f"quality 需更新 {len(report['updated'])} 期{updated}，"
                f"latest {'需更新' if report['latestUpdated'] else '一致'}，manifest {manifest_state}"
            )
    if args.dry_run:
        print("dry-run：未写入任何文件。")


if __name__ == "__main__":
    main()
