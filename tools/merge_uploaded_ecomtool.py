from __future__ import annotations

import argparse
import io
import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import urlopen

from openpyxl import load_workbook

from publish_snapshot import validate


ROOT = Path(__file__).parents[1]
BEIJING = timezone(timedelta(hours=8))
RANKING_HEADERS = ["ASIN", "图片ID", "标题", "价格", "评论数", "评分", "类目", "节点", "排名", "抓取时间"]


class TableReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.images: list[str | None] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.image: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row, self.image = [], None
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []
        elif tag == "img" and self.row is not None:
            self.image = dict(attrs).get("src")

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
                self.images.append(self.image)
            self.row = None


def read_bytes(source: str) -> bytes:
    if source.startswith("http://127.0.0.1/downloads/"):
        with urlopen(source, timeout=30) as response:
            return response.read()
    return Path(source).read_bytes()


def html_table(source: str) -> tuple[list[list[str]], list[str | None]]:
    parser = TableReader()
    parser.feed(read_bytes(source).decode("utf-8-sig"))
    return parser.rows, parser.images


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def best_seller_ranks(value: str) -> list[dict]:
    return [{"category": match.group(2).strip(), "rank": int(match.group(1).replace(",", ""))} for match in re.finditer(r"#([\d,]+)\s+in\s+(.+?)(?=\s+#|$)", value)]


def parse_date(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def high_resolution_image(value: str) -> str:
    match = re.fullmatch(r"(https://m\.media-amazon\.com/images/I/[A-Za-z0-9+_-]+)\.SR\d+,\d+\.jpg", value)
    return f"{match.group(1)}._AC_SL1000_.jpg" if match else value


def ranking_table(source: str) -> tuple[list[list[str]], str | None]:
    if source.lower().endswith(".json"):
        payload = json.loads(read_bytes(source).decode("utf-8-sig"))
        rows = payload.get("rows", [])
        if not isinstance(rows, list):
            raise SystemExit("上传榜单 JSON 缺少商品行")
        table = [RANKING_HEADERS]
        for row in rows:
            table.append([clean(row.get(key)) for key in ("asin", "imageId", "title", "price", "reviews", "rating", "category", "categoryNode", "rank", "capturedAt")])
        return table, clean(payload.get("sourceSha256")) or None
    rows, _ = html_table(source)
    return rows, None


def main() -> None:
    parser = argparse.ArgumentParser(description="合并用户上传的名次与真实 Ecomtool MCP 批量结果")
    parser.add_argument("--ranking", required=True, help="用户导出的 UTF-8 网页表格 .xls 或上传接口下载的 JSON")
    parser.add_argument("--product-info", required=True, help="Ecomtool amazon_get_product_info 的结果网址或本地文件")
    parser.add_argument("--market-analysis", required=True, help="Ecomtool amazon_get_market_analysis_data 的结果网址或本地文件")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--node", default="2368365011")
    args = parser.parse_args()
    try:
        args.output.resolve().relative_to(ROOT.resolve())
    except ValueError:
        raise SystemExit("输出文件必须位于 published-dashboard 中")

    ranking_rows, source_sha256 = ranking_table(args.ranking)
    if ranking_rows[0] != RANKING_HEADERS:
        raise SystemExit("榜单导出文件字段不符")
    ranking = {}
    captured_times = set()
    for row in ranking_rows[1:]:
        if len(row) != 10 or row[7] != args.node:
            raise SystemExit("榜单导出的类目节点或字段不一致")
        rank = int(row[8])
        if rank in ranking:
            raise SystemExit(f"榜单名次 {rank} 重复")
        ranking[rank] = row
        captured_times.add(row[9])
    if set(ranking) != set(range(1, 101)) or len({row[0] for row in ranking.values()}) != 100 or len(captured_times) != 1:
        raise SystemExit("榜单必须有 1–100 完整名次、100 个唯一 ASIN 和一致的采集时间")

    product_rows, product_images = html_table(args.product_info)
    product_headers = product_rows[0]
    products = {row[0]: (dict(zip(product_headers, row)), image) for row, image in zip(product_rows[1:], product_images[1:]) if len(row) == len(product_headers)}
    market_book = load_workbook(io.BytesIO(read_bytes(args.market_analysis)), read_only=True, data_only=True)
    market_rows = market_book.active.iter_rows(values_only=True)
    market_headers = [clean(value) for value in next(market_rows)]
    markets = {clean(row[0]): dict(zip(market_headers, row)) for row in market_rows if len(row) == len(market_headers) and clean(row[0])}
    expected = {row[0] for row in ranking.values()}
    if set(products) != expected or set(markets) != expected:
        raise SystemExit(json.dumps({"reason": "Ecomtool 结果 ASIN 不完整或不匹配", "productInfoMissing": sorted(expected - set(products)), "marketAnalysisMissing": sorted(expected - set(markets))}, ensure_ascii=False))

    items = []
    for rank, exported in sorted(ranking.items()):
        asin, image_id, title, exported_price = exported[:4]
        product, image = products[asin]
        market = markets[asin]
        if not image or not image.startswith("https://"):
            raise SystemExit(f"{asin} 的 Ecomtool 产品图缺失")
        ranks = best_seller_ranks(clean(product.get("销量排名信息")))
        if not ranks:
            main_rank = clean(market.get("大类排名")).replace(",", "")
            if main_rank.isdigit():
                ranks = [{"category": clean(market.get("大类")), "rank": int(main_rank)}]
        sub_ranks = ranks[1:]
        if not sub_ranks:
            sub_rank = clean(market.get("小类排名")).replace(",", "")
            if sub_rank.isdigit():
                sub_ranks = [{"category": clean(market.get("小类")), "rank": int(sub_rank)}]
        promotions = []
        coupon, discount, activity = (clean(product.get(key)) for key in ("Coupon", "促销折扣", "是否活动"))
        if coupon not in {"", "0", "nan", "None"}:
            promotions.append(f"Coupon {coupon}")
        if discount not in {"", "0", "nan", "None"}:
            promotions.append(f"促销折扣 {discount}")
        if activity not in {"", "nan", "None", "否"}:
            promotions.append(activity)
        price = clean(product.get("Buybox价格"))
        if not price or price.lower() in {"nan", "none"}:
            price = exported_price
        listing_date = parse_date(clean(market.get("上架日期")))
        item = {
            "rank": rank, "asin": asin, "title": clean(product.get("标题")) or title,
            "image": high_resolution_image(image), "imageExportMismatch": image_id not in image, "imageSource": "Ecomtool MCP 商品详情；同一图片 ID 的高清版本", "url": f"https://www.amazon.com/dp/{asin}",
            "brand": clean(product.get("品牌")) or clean(market.get("品牌名")) or "未显示/无法获取",
            "price": f"${price}" if price else "未显示/无法获取", "currency": "USD",
            "priceSource": "Ecomtool MCP 商品详情" if clean(product.get("Buybox价格")) else "用户上传榜单",
            "promotions": promotions, "promotionStatus": "detected" if promotions else "none",
            "listingDate": listing_date or "未显示/无法获取", "listingDateSource": "Ecomtool MCP 市场调研上架日期；非 Amazon Date First Available 核验",
            "mainCategory": ranks[0]["category"] if ranks else "未显示/无法获取",
            "mainBsr": ranks[0]["rank"] if ranks else "未显示/无法获取",
            "subCategory": sub_ranks[0]["category"] if sub_ranks else "未显示/无法获取",
            "subBsr": sub_ranks[0]["rank"] if sub_ranks else "未显示/无法获取",
            "subRanks": sub_ranks,
            "detailSource": "Ecomtool MCP 商品详情 + 市场调研", "detailStatus": "complete" if listing_date and ranks and sub_ranks else "partial", "detailAttempts": 2,
            "rankingSource": "用户上传榜单导出", "rankingCapturedAt": next(iter(captured_times)),
        }
        items.append(item)
    quality = validate(items, "Ecomtool MCP 商品详情 + 市场调研")
    if not quality["publishable"]:
        raise SystemExit(json.dumps(quality, ensure_ascii=False))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"count": len(items), "quality": quality, "rankingCapturedAt": next(iter(captured_times)), "rankingFileSha256": source_sha256, "detailsProcessedAt": datetime.now(BEIJING).isoformat(timespec="seconds"), "imageExportMismatches": sum(item["imageExportMismatch"] for item in items), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
