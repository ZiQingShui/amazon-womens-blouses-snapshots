from __future__ import annotations

import argparse
import io
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image


def probe(row: dict, min_height: int) -> dict:
    try:
        with urlopen(Request(row["image"], method="GET" if min_height else "HEAD"), timeout=15) as response:
            if response.status == 200 and (response.headers.get("content-type") or "").startswith("image/"):
                if min_height:
                    width, height = Image.open(io.BytesIO(response.read())).size
                    if height < min_height:
                        return {"rank": row["rank"], "ok": False, "reason": f"图片只有 {width}×{height} 像素"}
                return {"rank": row["rank"], "ok": True}
            return {"rank": row["rank"], "ok": False, "reason": f"HTTP {response.status} / {response.headers.get('content-type')}"}
    except Exception as error:
        return {"rank": row["rank"], "ok": False, "reason": str(error)[:90]}


def main() -> None:
    parser = argparse.ArgumentParser(description="发布前逐一确认图片网址能够直接返回图片")
    parser.add_argument("input", type=Path)
    parser.add_argument("--min-height", type=int, default=0)
    args = parser.parse_args()
    rows = json.loads(args.input.read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = [future.result() for future in as_completed([pool.submit(probe, row, args.min_height) for row in rows])]
    failed = sorted((row for row in results if not row["ok"]), key=lambda row: row["rank"])
    print(json.dumps({"count": len(rows), "imagesOk": len(rows) - len(failed), "failed": failed}, ensure_ascii=False))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
