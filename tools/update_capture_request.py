from __future__ import annotations

import argparse
import json
from pathlib import Path

from manual_capture_worker import DEFAULT_CONFIG, update_request


def main() -> None:
    parser = argparse.ArgumentParser(description="更新手动抓取任务状态")
    parser.add_argument("request_id")
    parser.add_argument("status", choices=("completed", "failed"))
    parser.add_argument("message")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    config["siteUrl"] = str(config["siteUrl"]).rstrip("/")
    result = update_request(config, args.request_id, args.status, args.message)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
