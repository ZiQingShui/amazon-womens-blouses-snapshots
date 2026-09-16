from __future__ import annotations

import argparse
import io
import json
import re
from decimal import Decimal, InvalidOperation
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


def parse_feedback(row: list[str]) -> tuple[int | None, float | None]:
    """Read review count and rating from the ranking export without inventing missing values."""
    reviews_raw, rating_raw = row[4].replace(",", "").strip(), row[5].strip()
    reviews = None
    rating = None
    if reviews_raw:
        if not reviews_raw.isdigit():
            raise ValueError(f"评论数不是非负整数：{row[4]}")
        reviews = int(reviews_raw)
    if rating_raw:
        try:
            rating = float(rating_raw)
        except ValueError as error:
            raise ValueError(f"评分不是数字：{row[5]}") from error
        if not 0 <= rating <= 5:
            raise ValueError(f"评分不在 0–5 范围内：{row[5]}")
    return reviews, rating


def valid_price(value: object) -> str | None:
    """Treat zero, unavailable labels and malformed values as missing prices."""
    raw = clean(value)
    if not re.fullmatch(r"\$?[\d,]+(?:\.\d+)?", raw):
        return None
    number = (raw[1:] if raw.startswith("$") else raw).replace(",", "")
    try:
        return number if Decimal(number) > 0 else None
    except InvalidOperation:
        return None


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
    parser.add_argument("--market-analysis", required=True, action="append", help="Ecomtool amazon_get_market_analysis_data 的结果网址或本地文件；分批结果可重复传入")
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
    markets = {}
    for source in args.market_analysis:
        market_book = load_workbook(io.BytesIO(read_bytes(source)), read_only=True, data_only=True)
        market_rows = market_book.active.iter_rows(values_only=True)
        market_headers = [clean(value) for value in next(market_rows)]
        for row in market_rows:
            asin = clean(row[0]) if row else ""
            if len(row) != len(market_headers) or not asin:
                continue
            if asin in markets:
                raise SystemExit(f"Ecomtool 分批市场分析结果 ASIN {asin} 重复")
            markets[asin] = dict(zip(market_headers, row))
        market_book.close()
    expected = {row[0] for row in ranking.values()}
    if set(products) != expected or set(markets) != expected:
        raise SystemExit(json.dumps({"reason": "Ecomtool 结果 ASIN 不完整或不匹配", "productInfoMissing": sorted(expected - set(products)), "marketAnalysisMissing": sorted(expected - set(markets))}, ensure_ascii=False))

    items = []
    for rank, exported in sorted(ranking.items()):
        asin, image_id, title, exported_price = exported[:4]
        try:
            review_count, rating = parse_feedback(exported)
        except ValueError as error:
            raise SystemExit(f"{asin} 的榜单反馈字段有误：{error}") from error
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
        raw_detail_price = clean(product.get("Buybox价格"))
        detail_price = valid_price(raw_detail_price)
        export_price = valid_price(exported_price)
        price = detail_price or export_price
        price_source = "Ecomtool MCP 商品详情" if detail_price else "用户上传榜单" if export_price else "未显示/无法获取"
        if detail_price:
            price_note = ""
        elif raw_detail_price in {"0", "0.0", "0.00"} and export_price:
            price_note = "详情价格 0 非有效报价；采用原始榜单价"
        elif exported_price == "不可售":
            price_note = "详情价格 0 非有效报价；原始榜单标记不可售" if raw_detail_price in {"0", "0.0", "0.00"} else "原始榜单标记不可售"
        else:
            price_note = "详情价格 0 非有效报价" if raw_detail_price in {"0", "0.0", "0.00"} else ""
        listing_date = parse_date(clean(market.get("上架日期")))
        image_export_mismatch = image_id not in image
        if image_export_mismatch and re.fullmatch(r"[A-Za-z0-9+_-]+", image_id):
            display_image = f"https://m.media-amazon.com/images/I/{image_id}._AC_SL1000_.jpg"
            image_source = "用户上传榜单图片 ID；经图片可访问性校验的高清版本"
        else:
            display_image = high_resolution_image(image)
            image_source = "Ecomtool MCP 商品详情；同一图片 ID 的高清版本"
        item = {
            "rank": rank, "asin": asin, "title": clean(product.get("标题")) or title,
            "image": display_image, "imageExportMismatch": image_export_mismatch, "imageSource": image_source, "url": f"https://www.amazon.com/dp/{asin}",
            "brand": clean(product.get("品牌")) or clean(market.get("品牌名")) or "未显示/无法获取",
            "price": f"${price}" if price else "未显示/无法获取", "currency": "USD",
            "priceSource": price_source,
            "priceNote": price_note,
            "rating": rating, "reviewCount": review_count, "feedbackSource": "用户上传榜单导出", "feedbackCapturedAt": next(iter(captured_times)),
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
