"""用 Ecomtool 的 amazon_get_product_info 批量抓取 ASIN 详情。

通过 HTTP JSON-RPC 直调 Ecomtool 服务（http://127.0.0.1/mcp/index.php），
异步任务模式：提交后返回结果链接，轮询链接直到表格生成，再解析成 JSON。

用法：
  python tools/fetch_ecomtool_details.py --asins work/all_asins.json --out work/ecomtool-details.json --batch 50
"""

from __future__ import annotations

import argparse
import json
import re
import time
import html
import urllib.request
from pathlib import Path

MCP_URL = "http://127.0.0.1/mcp/index.php"
POLL_INTERVAL = 5
POLL_TIMEOUT = 300


def rpc_call(method: str, params: dict) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        MCP_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_product_info(asins: list[str], site: str = "US") -> str:
    """提交抓取任务，返回结果链接。"""
    result = rpc_call("tools/call", {
        "name": "amazon_get_product_info",
        "arguments": {"asin": ",".join(asins), "site": site},
    })
    text = ""
    for block in result.get("result", {}).get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")
    match = re.search(r"http://127\.0\.0\.1/downloads/[^\s\n]+", text)
    if not match:
        raise RuntimeError(f"未从响应中提取到结果链接：{text[:500]}")
    return match.group(0)


def poll_result(url: str) -> str:
    """轮询结果链接直到有内容，返回 HTML 文本。"""
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            if len(raw) > 2000 and "<table" in raw:
                return raw
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"轮询结果超时：{url}")


def parse_detail_table(raw: str) -> list[dict]:
    """解析详情表格 HTML，返回字段名 -> 值的列表。"""
    # 表头
    head = re.search(r"<thead[^>]*>(.*?)</thead>", raw, re.S | re.I)
    if not head:
        return []
    ths = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", head.group(1), re.S | re.I)
    headers = [html.unescape(re.sub(r"<[^>]*>", "", c)).strip() for c in ths]

    rows = []
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", raw, re.S | re.I):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S | re.I)
        if not tds:
            continue
        vals = [html.unescape(re.sub(r"<[^>]*>", "", c)).strip() for c in tds]
        if vals and vals[0] != "ASIN" and len(vals) == len(headers):
            rows.append(dict(zip(headers, vals)))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asins", required=True, type=Path, help="ASIN 清单 JSON（数组）")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--batch", type=int, default=50)
    parser.add_argument("--site", default="US")
    args = parser.parse_args()

    asins = json.loads(args.asins.read_text(encoding="utf-8"))
    print(f"共 {len(asins)} 个 ASIN，每批 {args.batch} 个，共 {(len(asins) + args.batch - 1) // args.batch} 批")

    all_details = {}
    for i in range(0, len(asins), args.batch):
        batch = asins[i:i + args.batch]
        batch_no = i // args.batch + 1
        print(f"\n=== 第 {batch_no} 批：{len(batch)} 个 ASIN ===")
        try:
            url = fetch_product_info(batch, args.site)
            print(f"  任务已提交，结果链接：{url}")
            raw = poll_result(url)
            rows = parse_detail_table(raw)
            print(f"  解析到 {len(rows)} 条详情")
            for row in rows:
                asin = row.get("ASIN", "").strip().upper()
                if asin:
                    all_details[asin] = row
            time.sleep(2)
        except Exception as e:
            print(f"  批次失败：{e}")
        # 每批之间休息，避免给 Ecomtool 太大压力
        time.sleep(3)

    # ⚠ 有缺口时**不覆盖**上次成功的结果：以前批失败也照写，把好数据冲掉且退出码仍是 0
    missing = [a for a in asins if a not in all_details]
    if missing:
        part = args.out.parent / (args.out.name + ".partial")
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_text(json.dumps(all_details, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n✗ 缺 %d 个 ASIN 的详情，拒绝覆盖 %s" % (len(missing), args.out))
        print("  缺失示例：%s" % missing[:8])
        print("  部分结果已写到 %s（排查用）" % part)
        raise SystemExit(1)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(all_details, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成：成功抓取 {len(all_details)}/{len(asins)} 个 ASIN 详情")
    print(f"结果已保存到 {args.out}")


if __name__ == "__main__":
    main()
