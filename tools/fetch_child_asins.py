"""用 Ecomtool 的 amazon_get_all_child_asin 批量抓取「同款变体组」。

**不依赖卖家精灵** —— 卖家精灵账号不可用（被封/过期/额度用尽）时，这是唯一能拿到
「哪些商品属于同一款」的途径。抓取结果用于：
  1. 跨期比对时判断同款（同一组的商品应视为一条）
  2. 借助「组内兄弟的历史市场调研记录」回填父体 ASIN 与上架日期

返回结构（保持服务端原始语义）：
  { "某个ASIN": ["它所在变体组的全部子ASIN", ...], ... }
组内包含输入的 ASIN 本身，但**不含**真实父 ASIN（实测：3 个样本的 parentAsin 均不在组内），
所以无法用它反推 parentAsin，只能做「同组」判定。

用法：
  python tools/fetch_child_asins.py --asins work/2026-09-19_all_asins.json \
      --out work/child-asins-2026-09-19.json --batch 60
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.request
from pathlib import Path

MCP_URL = "http://127.0.0.1/mcp/index.php"
POLL_INTERVAL = 3
POLL_TIMEOUT = 300


def rpc_call(method: str, params: dict) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        MCP_URL,
        data=payload,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def submit(asins: list[str], site: str = "US") -> str:
    result = rpc_call("tools/call", {
        "name": "amazon_get_all_child_asin",
        "arguments": {"asin": ",".join(asins), "site": site},
    })
    text = ""
    for block in result.get("result", {}).get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")
    match = re.search(r"http://127\.0\.0\.1/downloads/[^\s\n]+\.html", text)
    if not match:
        raise RuntimeError("未提取到结果链接：%s" % text[:400])
    return match.group(0)


def poll_html(url: str) -> str:
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            # ⚠ 原来写成 `A and B or A`，因 and 优先于 or，等价于只看长度 —— B 是死条件，
            #   任何 >3KB 的错误页/等待页都会被当成有效结果，解析出 0 行却退出码 0
            if len(body) > 3000 and "result_table" in body:
                return body
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
    raise TimeoutError("轮询超时：%s" % url)


def cells(row_html: str) -> list[str]:
    out = []
    for m in re.finditer(r"<td\b[^>]*>([\s\S]*?)</td>", row_html, re.I):
        out.append(html.unescape(re.sub(r"<[^>]*>", "", m.group(1))).strip())
    return out


def parse(body: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"<tr\b[^>]*>([\s\S]*?)</tr>", body, re.I):
        c = cells(m.group(1))
        if len(c) < 2 or c[0] == "ASIN":
            continue
        children = [x.strip() for x in c[1].split(",") if x.strip()]
        if c[0]:
            out[c[0]] = children
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asins", required=True, help="ASIN 列表 JSON 文件（数组）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch", type=int, default=60)
    ap.add_argument("--site", default="US")
    args = ap.parse_args()

    asins = json.loads(Path(args.asins).read_text(encoding="utf-8"))
    batches = [asins[i:i + args.batch] for i in range(0, len(asins), args.batch)]
    print("共 %d 个 ASIN，每批 %d 个，共 %d 批\n" % (len(asins), args.batch, len(batches)))

    collected: dict[str, list[str]] = {}
    for i, batch in enumerate(batches, 1):
        print("=== 第 %d 批：%d 个 ===" % (i, len(batch)))
        try:
            url = submit(batch, args.site)
            print("  已提交：%s" % url)
            data = parse(poll_html(url))
            collected.update(data)
            print("  解析到 %d 条" % len(data))
        except Exception as e:  # noqa: BLE001
            print("  批次失败：%s" % e)

    missing = [a for a in asins if a not in collected]
    if missing:
        part = Path(str(args.out) + ".partial")
        part.write_text(json.dumps(collected, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n✗ 缺 %d 个 ASIN 的变体组，拒绝覆盖 %s" % (len(missing), args.out))
        print("  缺失示例：%s" % missing[:8])
        print("  部分结果已写到 %s（排查用）" % part)
        raise SystemExit(1)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(collected, ensure_ascii=False, indent=1), encoding="utf-8")
    sizes = sorted(len(v) for v in collected.values())
    print("\n完成：%d/%d 个 ASIN 拿到变体组" % (len(collected), len(asins)))
    if sizes:
        print("组大小：最小 %d / 中位 %d / 最大 %d" % (sizes[0], sizes[len(sizes) // 2], sizes[-1]))
    print("结果已保存到 %s" % args.out)


if __name__ == "__main__":
    main()
