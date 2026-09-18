from __future__ import annotations

"""半自动采集编排：Ecomtool 抓数产出 enriched JSON 后，一键校验并发布。

流程：
1. 读取 enriched JSON（Ecomtool 抓数产出，格式见 capture_guide.md）
2. 逐项图片真实性检查（check_snapshot_images）
3. 调用 publish_snapshot 做完整校验并落盘 docs/dist、重建 Worker

用法：
  python tools/capture.py --input work/enriched-2026-09-17.json --date 2026-09-17
  python tools/capture.py --input ... --node 2368383011 --ranking best-sellers
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parents[1]
DEFAULT_NODE = "2368365011"
RANKINGS = {"new-releases": "新品榜", "best-sellers": "热销榜"}


def fail(message: str) -> None:
    print(f"[失败] {message}")
    sys.exit(1)


def load_items(path: Path) -> list[dict]:
    try:
        raw = path.read_text(encoding="utf-8-sig").strip()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"无法读取输入文件 {path}：{error}")
    if isinstance(payload, dict):
        payload = payload.get("items", payload.get("new_releases"))
    if not isinstance(payload, list) or not payload:
        fail("输入文件必须包含商品数组（items）")
    return payload


def basic_check(items: list[dict], expected_node: str) -> None:
    count = len(items)
    ranks = [item.get("rank") for item in items]
    asins = [str(item.get("asin", "")).strip().upper() for item in items]
    missing_ranks = sorted(set(range(1, 101)) - {r for r in ranks if isinstance(r, int)})
    print(f"  商品数：{count}")
    if count != 100:
        fail(f"需要 100 个商品，当前 {count}")
    if missing_ranks:
        fail(f"名次缺失：{missing_ranks}")
    if len(set(asins)) != 100:
        fail("ASIN 存在重复")
    unchecked = sum(1 for item in items if item.get("detailStatus") not in {"complete", "partial"} or int(item.get("detailAttempts", 0)) < 1)
    if unchecked:
        fail(f"有 {unchecked} 个商品未完成详情采集（detailStatus/detailAttempts 缺失）")
    print("  名次 1-100 完整、ASIN 唯一、详情采集齐全 ✓")


def run_cmd(args: list[str], description: str) -> None:
    print(f"\n▶ {description}")
    result = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        fail(f"{description} 未通过：{detail[-800:]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="半自动采集编排：校验 enriched JSON 并发布到看板")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--date", required=True)
    parser.add_argument("--node", default=DEFAULT_NODE)
    parser.add_argument("--ranking", choices=list(RANKINGS), default="new-releases")
    parser.add_argument("--skip-image-check", action="store_true", help="跳过图片逐项检查（仅用于快速重试）")
    args = parser.parse_args()

    datetime.strptime(args.date, "%Y-%m-%d")
    input_path = args.input.resolve()
    print(f"=== 半自动采集 · {args.date} · 节点 {args.node} · {RANKINGS[args.ranking]} ===")

    items = load_items(input_path)
    print("校验输入结构…")
    basic_check(items, args.node)

    if not args.skip_image_check:
        run_cmd(
            [sys.executable, str(ROOT / "tools" / "check_snapshot_images.py"), str(input_path), "--min-height", "300"],
            "逐项检查图片是否真实可访问（≥300px）",
        )

    # 热销榜暂不支持（publish_snapshot 目前只发布新品榜）
    if args.ranking != "new-releases":
        fail("当前 publish_snapshot 仅支持新品榜；热销榜发布尚未实现")

    publish_args = [
        sys.executable, str(ROOT / "tools" / "publish_snapshot.py"),
        "--input", str(input_path),
        "--date", args.date,
        "--node", args.node,
    ]
    captured_at = f"{args.date}T08:30:00+08:00"
    publish_args += ["--captured-at", captured_at]

    run_cmd(publish_args, "校验并发布到 docs/dist（含 Worker 重建）")
    print(f"\n✅ 发布完成：{args.date} · 节点 {args.node}。刷新看板即可看到新快照。")


if __name__ == "__main__":
    main()
