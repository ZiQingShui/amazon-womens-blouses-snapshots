"""用 Ecomtool 的 amazon_get_market_analysis_data 批量抓取父ASIN + 上架日期。

结果 .xlsx 含：竞品ASIN、父ASIN、上架日期、变体数、品牌名、大类/小类排名等。
用于给快照补上「父体映射」和「上架日期」字段。

用法：
  python tools/fetch_market_analysis.py --asins work/today_all_asins.json --out work/market-analysis-2026-09-18.json --batch 60
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

import openpyxl

MCP_URL = "http://127.0.0.1/mcp/index.php"
POLL_INTERVAL = 5
POLL_TIMEOUT = 300


def rpc_call(method: str, params: dict) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        MCP_URL,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_market(asins: list[str], site: str = "US") -> str:
    result = rpc_call("tools/call", {
        "name": "amazon_get_market_analysis_data",
        "arguments": {"asin": ",".join(asins), "site": site},
    })
    text = ""
    for block in result.get("result", {}).get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")
    match = re.search(r"http://127\.0\.0\.1/downloads/[^\s\n]+\.xlsx", text)
    if not match:
        raise RuntimeError(f"未提取到结果链接：{text[:400]}")
    return match.group(0)


def poll_xlsx(url: str) -> bytes:
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read()
            if len(data) > 3000:
                return data
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"轮询超时：{url}")


def parse_xlsx(data: bytes) -> dict[str, dict]:
    import io
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    headers = [str(h).strip() if h is not None else "" for h in next(rows)]
    idx = {name: i for i, name in enumerate(headers)}

    def get(row, name):
        i = idx.get(name)
        return row[i] if i is not None and i < len(row) else None

    out = {}
    for row in rows:
        asin = get(row, "竞品ASIN")
        if not asin:
            continue
        out[str(asin).strip().upper()] = {
            "parentAsin": get(row, "父ASIN"),
            "listingDate": _norm_date(get(row, "上架日期")),
            "variantCount": get(row, "变体数"),
            "salesDays": get(row, "销售天数"),
            "brand": get(row, "品牌名"),
        }
    return out


def _norm_date(value):
    if not value:
        return None
    s = str(value).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return s


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asins", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--batch", type=int, default=60)
    parser.add_argument("--site", default="US")
    args = parser.parse_args()

    asins = json.loads(args.asins.read_text(encoding="utf-8"))
    print(f"共 {len(asins)} 个 ASIN，每批 {args.batch} 个，共 {(len(asins)+args.batch-1)//args.batch} 批")

    all_data = {}
    for i in range(0, len(asins), args.batch):
        batch = asins[i:i + args.batch]
        batch_no = i // args.batch + 1
        print(f"\n=== 第 {batch_no} 批：{len(batch)} 个 ===")
        try:
            url = fetch_market(batch, args.site)
            print(f"  已提交：{url}")
            data = poll_xlsx(url)
            parsed = parse_xlsx(data)
            all_data.update(parsed)
            print(f"  解析到 {len(parsed)} 条")
            time.sleep(2)
        except Exception as e:
            print(f"  批次失败：{e}")
        time.sleep(2)

    # ⚠ 有缺口时不覆盖上次成功的结果（父体/上架日期缺一个都会让覆盖率掉下来）
    missing = [a for a in asins if a not in all_data]
    if missing:
        part = args.out.parent / (args.out.name + ".partial")
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n✗ 缺 %d 个 ASIN 的父体数据，拒绝覆盖 %s" % (len(missing), args.out))
        print("  缺失示例：%s" % missing[:8])
        print("  部分结果已写到 %s（排查用）" % part)
        raise SystemExit(1)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成：成功 {len(all_data)}/{len(asins)}，保存到 {args.out}")


if __name__ == "__main__":
    main()
