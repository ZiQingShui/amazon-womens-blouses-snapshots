from __future__ import annotations

import argparse
import json
from pathlib import Path

from manual_capture_worker import DEFAULT_CONFIG, api, load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="读取上传并校验过的新品榜名次，不包含商品详情")
    parser.add_argument("request_id")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(Path(__file__).parents[1].resolve()):
        raise SystemExit("输出文件必须位于 published-dashboard 中")
    config = load_config(args.config.resolve())
    payload = api(config, f"/api/ranking-uploads/{args.request_id}")
    rows = payload.get("rows", [])
    if len(rows) != 100 or [row.get("rank") for row in rows] != list(range(1, 101)):
        raise SystemExit("上传榜单在下载后未通过 Top 100 完整性校验")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"requestId": args.request_id, "count": len(rows), "sourceCapturedAt": payload.get("sourceCapturedAt"), "sourceSha256": payload.get("sourceSha256"), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
