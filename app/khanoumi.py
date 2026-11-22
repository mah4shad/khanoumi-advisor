
"""
Temporary local demo catalog.
All products, brands, prices and stock information are fictional.
"""

from __future__ import annotations

import re
from typing import Any


class CatalogError(RuntimeError):
    """Error raised when the product catalog is unavailable."""


DEMO_PRODUCTS = [
    {
        "id": "D001",
        "title": "کرم مرطوب کننده پوست چرب",
        "brand": "درمالیفت",
        "final_price": 185000,
        "price": 220000,
        "discount_pct": 16,
        "in_stock": True,
        "image": "",
        "url": "",
        "slug": "D001",
    },
    {
        "id": "D002",
        "title": "کرم آبرسان پوست خشک",
        "brand": "هیدرودرم",
        "final_price": 240000,
        "price": 240000,
        "discount_pct": 0,
        "in_stock": True,
        "image": "",
        "url": "",
        "slug": "D002",
    },
    {
        "id": "D003",
        "title": "ژل مرطوب کننده پوست مختلط",
        "brand": "آردن",
        "final_price": 195000,
        "price": 210000,
        "discount_pct": 7,
        "in_stock": True,
        "image": "",
        "url": "",
        "slug": "D003",
    },
    {
        "id": "D004",
        "title": "ضد آفتاب بی رنگ پوست چرب SPF50",
        "brand": "سان سیف",
        "final_price": 310000,
        "price": 350000,
        "discount_pct": 11,
        "in_stock": True,
        "image": "",
        "url": "",
        "slug": "D004",
    },
    {
        "id": "D005",
        "title": "ضد آفتاب رنگی پوست خشک SPF50",
        "brand": "درمالیفت",
        "final_price": 285000,
        "price": 285000,
        "discount_pct": 0,
        "in_stock": True,
        "image": "",
        "url": "",
        "slug": "D005",
    },
    {
        "id": "D006",
        "title": "سرم نیاسینامید مناسب پوست چرب",
        "brand": "اورموس",
        "final_price": 390000,
        "price": 450000,
        "discount_pct": 13,
        "in_stock": True,
        "image": "",
        "url": "",
        "slug": "D006",
    },
    {
        "id": "D007",
        "title": "سرم هیالورونیک اسید آبرسان",
        "brand": "آردن",
        "final_price": 420000,
        "price": 420000,
        "discount_pct": 0,
        "in_stock": True,
        "image": "",
        "url": "",
        "slug": "D007",
    },
    {
        "id": "D008",
        "title": "سرم ویتامین C روشن کننده",
        "brand": "درمالیفت",
        "final_price": 510000,
        "price": 560000,
        "discount_pct": 9,
        "in_stock": True,
        "image": "",
        "url": "",
        "slug": "D008",
    },
    {
        "id": "D009",
        "title": "ژل شستشوی صورت پوست چرب",
        "brand": "هیدرودرم",
        "final_price": 145000,
        "price": 160000,
        "discount_pct": 9,
        "in_stock": True,
        "image": "",
        "url": "",
        "slug": "D009",
    },
    {
        "id": "D010",
        "title": "فوم شستشوی صورت پوست خشک",
        "brand": "آردن",
        "final_price": 175000,
        "price": 175000,
        "discount_pct": 0,
        "in_stock": True,
        "image": "",
        "url": "",
        "slug": "D010",
    },
    {
        "id": "D011",
        "title": "کرم ضد لک و روشن کننده",
        "brand": "درمالیفت",
        "final_price": 480000,
        "price": 520000,
        "discount_pct": 8,
        "in_stock": True,
        "image": "",
        "url": "",
        "slug": "D011",
    },
    {
        "id": "D012",
        "title": "کرم دور چشم آبرسان",
        "brand": "هیدرودرم",
        "final_price": 260000,
        "price": 290000,
        "discount_pct": 10,
        "in_stock": True,
        "image": "",
        "url": "",
        "slug": "D012",
    },
    {
        "id": "D013",
        "title": "کرم ضد جوش پوست چرب",
        "brand": "آردن",
        "final_price": 225000,
        "price": 250000,
        "discount_pct": 10,
        "in_stock": True,
        "image": "",
        "url": "",
        "slug": "D013",
    },
    {
        "id": "D014",
        "title": "کرم مرطوب کننده پوست حساس",
        "brand": "هیدرودرم",
        "final_price": 205000,
        "price": 230000,
        "discount_pct": 11,
        "in_stock": True,
        "image": "",
        "url": "",
        "slug": "D014",
    },
    {
        "id": "D015",
        "title": "لوسیون آبرسان صورت و بدن",
        "brand": "آردن",
        "final_price": 290000,
        "price": 320000,
        "discount_pct": 9,
        "in_stock": False,
        "image": "",
        "url": "",
        "slug": "D015",
    },
]


def _normalize(value: str) -> str:
    value = str(value or "").lower()
    value = value.replace("ي", "ی").replace("ك", "ک")
    value = re.sub(r"[^\w\s]", " ", value)
    value = " ".join(value.split())

    # یکسان‌سازی شکل‌های مختلف نوشتن ضدآفتاب
    value = value.replace("ضد آفتاب", "ضدآفتاب")

    return value


def search(
    query: str,
    limit: int = 20,
    max_price: int | None = None,
    in_stock: bool = True,
) -> list[dict[str, Any]]:

    query = _normalize(query)

    if len(query) < 2:
        return []

    words = query.split()
    results = []

    for product in DEMO_PRODUCTS:

        if in_stock and not product["in_stock"]:
            continue

        if max_price is not None and product["final_price"] > max_price:
            continue

        searchable = _normalize(
            product["title"] + " " +
            product["brand"] + " " +
            product["slug"]
        )

        score = sum(
            1 for word in words
            if word in searchable
        )

        if score > 0:
            results.append((score, product))

    results.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return [
        dict(product)
        for _, product in results[:limit]
    ]


def collect(
    queries: list[str],
    limit: int = 50,
    max_price: int | None = None,
) -> list[dict[str, Any]]:

    merged = []
    seen = set()

    for query in queries:

        products = search(
            query,
            limit=limit,
            max_price=max_price,
        )

        for product in products:

            product_id = product["id"]

            if product_id in seen:
                continue

            seen.add(product_id)

            merged.append({
                **product,
                "matched_query": query,
            })

            if len(merged) >= limit:
                return merged

    return merged


def refresh(
    products: list[dict[str, Any]]
) -> list[dict[str, Any]]:

    return [
        dict(product)
        for product in products
    ]


def showcase(
    limit: int = 50
) -> list[dict[str, Any]]:

    return [
        dict(product)
        for product in DEMO_PRODUCTS[:limit]
    ]
