"""用 Ecomtool 的「ASIN 监控」链路抓促销数据（Coupon / Promotion折扣 / 是否Deal）。

背景：`amazon_get_product_info` 的 Coupon 列自 2026-09-18 起恒为 0（服务端解析失效，
原始 HTML 里那一列就是 0）。但 Ecomtool 的 **ASIN 监控系统**另有一套抓取，
38 列里含 Coupon / Promotion折扣 / 是否Deal，实测正常 —— 本脚本走的就是这条链路。

链路三步（缺一不可）：
  1. `amz_asin_tracking_add_task`   把 ASIN 加入监控队列（ASIN 不在监控里就没有任何数据）
  2. `ect_run_server_tasks`         触发监控系统跑一次（约 5 秒/ASIN，异步，不等结果页）
  3. `amz_asin_tracking_export_asin_data`  导出 CSV

用法：
  # 首次 / 有新上榜商品：加入监控并触发抓取（跑完等 --wait 秒再导出）
  python tools/fetch_asin_tracking.py --asins work/2026-09-20_all_asins.json \
      --out work/asin-tracking-2026-09-20.json --add --run --wait 180

  # 已在监控里（例行）：只导出，不重复添加
  python tools/fetch_asin_tracking.py --asins work/2026-09-20_all_asins.json \
      --out work/asin-tracking-2026-09-20.json

输出 JSON：{ASIN: {coupon, couponPct, promoDiscount, deal, price, finalPrice, capturedAt, ...}}
注意：coupon / promoDiscount 是**百分比字符串**（如 "10%"），与 product_info 的美元值不同；
两者可互相换算（$24.99 × 10% = $2.50）。
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import time
import urllib.request
from pathlib import Path

MCP_URL = "http://127.0.0.1/mcp/index.php"
POLL_INTERVAL = 5
POLL_TIMEOUT = 420

# CSV 表头（37 列，实测）
COL_ASIN = "子ASIN"
COL_COUPON = "Coupon"
COL_PROMO = "Promotion折扣"
COL_DEAL = "是否Deal"
COL_PRICE = "价格"
COL_FINAL = "最终价格"
COL_CAPTURED = "抓取时间"


def rpc_call(method: str, params: dict) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        MCP_URL,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_tool(name: str, args: dict) -> str:
    """调一次 MCP 工具，返回拼好的文本；顺带取出结果链接。"""
    result = rpc_call("tools/call", {"name": name, "arguments": args})
    text = ""
    for block in result.get("result", {}).get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")
    return text


def poll_page(url: str, want_csv: bool) -> str:
    """轮询结果链接。want_csv=True 时等到内容像 CSV；否则等到有实质内容即可。

    服务端在任务未完成时会先返回监控系统的网页（不是报错），所以要靠内容判定。
    """
    deadline = time.time() + POLL_TIMEOUT
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=40) as resp:
                raw = resp.read().decode("utf-8-sig", "replace")
            last = raw
            if len(raw) > 200:
                if not want_csv:
                    return raw
                head = raw.lstrip()[:80]
                if head.startswith("ID,") or head.startswith("ID\t") or "子ASIN" in head:
                    return raw
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
    return last


def parse_csv(raw: str) -> dict[str, dict]:
    return _rows_to_dict(csv.reader(io.StringIO(raw)), None)


def parse_html_table(raw: str) -> dict[str, dict]:
    """兜底：任务未完成时拿到的是监控网页，把里面的表格抠出来。"""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.S)
    cells = []
    for r in rows:
        tds = [html.unescape(re.sub(r"<[^>]+>", "", x)).strip() for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]
        if len(tds) > 12:
            cells.append(tds)
    if not cells:
        return {}
    header = cells[0]
    return _rows_to_dict(iter(cells[1:]), header)


def _rows_to_dict(rows, header) -> dict[str, dict]:
    rows = list(rows)
    if not rows:
        return {}
    if header is None:
        header = [c.strip() for c in rows[0]]
        body = rows[1:]
    else:
        body = rows

    def find(col):
        for i, t in enumerate(header):
            if t == col or col in t:
                return i
        return None

    ia, ic, ip, idl, ipr, ifin, ica = (
        find(COL_ASIN), find(COL_COUPON), find(COL_PROMO), find(COL_DEAL),
        find(COL_PRICE), find(COL_FINAL), find(COL_CAPTURED),
    )
    if ia is None:
        return {}

    def cell(row, i):
        return row[i] if i is not None and i < len(row) else ""

    def num(s):
        m = re.search(r"-?\d+(?:\.\d+)?", str(s or ""))
        return float(m.group(0)) if m else None

    out: dict[str, dict] = {}
    for row in body:
        asin = str(cell(row, ia)).strip().upper()
        if not asin or len(asin) < 9:
            continue
        coupon = str(cell(row, ic)).strip()
        promo = str(cell(row, ip)).strip()
        deal_raw = str(cell(row, idl)).strip()
        coupon_pct = num(coupon) if coupon not in ("0", "", "-") else 0.0
        out[asin] = {
            "coupon": "" if coupon in ("0", "", "-") else coupon,
            "couponPct": coupon_pct,
            "promoDiscount": "" if promo in ("0", "", "-") else promo,
            "promoPct": (num(promo) or 0.0) if promo not in ("0", "", "-") else 0.0,
            "deal": deal_raw not in ("0", "", "-", "N", "n"),
            "dealRaw": deal_raw,
            "price": num(cell(row, ipr)),
            "finalPrice": num(cell(row, ifin)),
            "capturedAt": str(cell(row, ica)).strip(),
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asins", required=True, type=Path, help="ASIN 列表 JSON")
    parser.add_argument("--out", required=True, type=Path, help="输出 JSON")
    parser.add_argument("--batch", type=int, default=60, help="每批多少个（添加与导出共用）")
    parser.add_argument("--site", default="US")
    parser.add_argument("--add", action="store_true", help="先加入监控队列（不在监控里就没有数据）")
    parser.add_argument("--run", action="store_true", help="触发监控系统抓取一次")
    parser.add_argument("--wait", type=int, default=180, help="--run 之后等待多少秒再导出（约 5 秒/ASIN）")
    parser.add_argument("--date-from", default=None, help="导出开始日期 yyyy-mm-dd")
    parser.add_argument("--date-to", default=None, help="导出结束日期 yyyy-mm-dd")
    args = parser.parse_args()

    asins = [str(a).strip().upper() for a in json.loads(args.asins.read_text(encoding="utf-8"))]
    batches = [asins[i:i + args.batch] for i in range(0, len(asins), args.batch)]
    print(f"共 {len(asins)} 个 ASIN，分 {len(batches)} 批（每批 {args.batch}）")

    if args.add:
        print("\n=== ① 加入监控队列 ===")
        for i, b in enumerate(batches, 1):
            try:
                text = call_tool("amz_asin_tracking_add_task",
                                 {"asin": b, "site": args.site, "mcp_server": "local"})
                # 立即返回的是「任务已提交」回执；「运行成功」要等结果页才有
                if "任务已成功提交" in text or "运行成功" in text:
                    state = "已提交"
                else:
                    state = "返回异常：" + text.strip().splitlines()[0][:110]
                print(f"  第 {i}/{len(batches)} 批（{len(b)} 个）：{state}")
            except Exception as e:
                print(f"  第 {i}/{len(batches)} 批失败：{e}")
            time.sleep(1)

    if args.run:
        print("\n=== ② 触发监控系统抓取 ===")
        try:
            text = call_tool("ect_run_server_tasks", {"mcp_server": "local"})
            print(f"  已提交：{text.strip().splitlines()[0][:100]}")
        except Exception as e:
            print(f"  提交失败：{e}")
        print(f"  等待 {args.wait} 秒让监控系统跑完（约 5 秒/ASIN）…")
        time.sleep(args.wait)

    print("\n=== ③ 导出监控数据 ===")
    all_data: dict[str, dict] = {}
    for i, b in enumerate(batches, 1):
        try:
            text = call_tool("amz_asin_tracking_export_asin_data",
                             {"asin": ",".join(b), "site": args.site, "mcp_server": "local",
                              **({"date_from": args.date_from} if args.date_from else {}),
                              **({"date_to": args.date_to} if args.date_to else {})})
            m = re.search(r"http://127\.0\.0\.1/downloads/[^\s\n\)\"]+\.csv", text)
            if not m:
                print(f"  第 {i}/{len(batches)} 批：未取到结果链接 {text[:120]}")
                continue
            url = m.group(0)
            raw = poll_page(url, want_csv=True)
            parsed = parse_csv(raw) or parse_html_table(raw)
            all_data.update(parsed)
            print(f"  第 {i}/{len(batches)} 批：{len(parsed)} 条")
        except Exception as e:
            print(f"  第 {i}/{len(batches)} 批失败：{e}")
        time.sleep(2)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8")

    with_coupon = [k for k, v in all_data.items() if v["coupon"]]
    with_deal = [k for k, v in all_data.items() if v["deal"]]
    with_promo = [k for k, v in all_data.items() if v["promoDiscount"]]
    print(f"\n完成：{len(all_data)}/{len(asins)} 个 ASIN 有数据 → {args.out}")
    print(f"  Coupon 非空 {len(with_coupon)} ／ Promotion折扣 非空 {len(with_promo)} ／ 是 Deal {len(with_deal)}")
    missing = [a for a in asins if a not in all_data]
    if missing:
        print(f"  未出现（通常=尚未被抓到，可稍后重跑）：{len(missing)} 个，例如 {missing[:5]}")


if __name__ == "__main__":
    main()
