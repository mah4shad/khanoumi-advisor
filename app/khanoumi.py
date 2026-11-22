
"""Live beauty product catalog from Rozalin Beauty WooCommerce Store API."""

from __future__ import annotations

import re
import time
from html import unescape
from typing import Any

import httpx


BASE = "https://rozalinbeauty.com"
PRODUCTS = f"{BASE}/wp-json/wc/store/v1/products"

CACHE_TTL = 180
CACHE_MAX = 400

SHOWCASE_QUERIES = [
    "مرطوب کننده",
    "ضد آفتاب",
    "سرم",
    "شوینده",
    "ضد جوش",
    "ضد لک",
]

_cache: dict[str, tuple[float, Any]] = {}


class CatalogError(RuntimeError):
    """Error raised when the product catalog is unavailable."""


def _normalize(value: str) -> str:
    value = str(value or "").lower()
    value = value.replace("ي", "ی").replace("ك", "ک")
    value = value.replace("ضد آفتاب", "ضدآفتاب")
    value = re.sub(r"[^\w\s]", " ", value)
    return " ".join(value.split())


def _cache_get(key: str):
    item = _cache.get(key)

    if not item:
        return None

    expires, value = item

    if time.time() > expires:
        _cache.pop(key, None)
        return None

    return value


def _cache_put(key: str, value: Any):
    if len(_cache) >= CACHE_MAX:
        _cache.clear()

    _cache[key] = (time.time() + CACHE_TTL, value)


def _get_json(params: dict[str, Any]) -> list[dict[str, Any]]:
    cache_key = str(sorted(params.items()))

    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        response = httpx.get(
            PRODUCTS,
            params=params,
            timeout=30,
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
            },
        )
        response.raise_for_status()
        data = response.json()

    except httpx.HTTPError as exc:
        raise CatalogError(
            f"اتصال به فروشگاه رزالین برقرار نشد: {exc.__class__.__name__}"
        ) from exc

    except ValueError as exc:
        raise CatalogError("پاسخ فروشگاه معتبر نیست.") from exc

    if not isinstance(data, list):
        raise CatalogError("ساختار اطلاعات دریافتی از فروشگاه معتبر نیست.")

    _cache_put(cache_key, data)
    return data


def _int(value: Any) -> int | None:
    if value is None:
        return None

    try:
        value = str(value).strip().replace(",", "")
        number = int(float(value))
        return number if number > 0 else None
    except (ValueError, TypeError):
        return None


def _money(prices: dict[str, Any]) -> tuple[int | None, int | None]:
    """
    Returns:
        final_price, regular_price

    WooCommerce may return zero for unavailable prices.
    Zero is never treated as a real product price.
    """
    current = _int(prices.get("price"))
    regular = _int(prices.get("regular_price"))
    sale = _int(prices.get("sale_price"))

    if current is not None:
        final_price = current
    elif sale is not None:
        final_price = sale
    else:
        final_price = regular

    if regular is None:
        regular = final_price

    return final_price, regular


def _clean_html(value: Any) -> str:
    value = unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(value.split())


def _brand(product: dict[str, Any]) -> str:
    brands = product.get("brands") or []

    if isinstance(brands, list) and brands:
        first = brands[0]

        if isinstance(first, dict):
            return str(first.get("name") or "")

        return str(first)

    return ""


def _item(product: dict[str, Any]) -> dict[str, Any]:
    prices = product.get("prices") or {}

    final_price, regular_price = _money(prices)

    discount = 0
    if final_price and regular_price and regular_price > final_price:
        discount = round(
            (regular_price - final_price) / regular_price * 100
        )

    images = product.get("images") or []
    image = ""

    if images and isinstance(images[0], dict):
        image = str(images[0].get("src") or "")

    categories = [
        str(category.get("name") or "")
        for category in product.get("categories") or []
        if isinstance(category, dict)
    ]

    return {
        "id": str(product.get("id") or ""),
        "slug": str(product.get("slug") or ""),
        "title": str(product.get("name") or ""),
        "brand": _brand(product),
        "final_price": final_price,
        "price": regular_price,
        "discount_pct": discount,
        "in_stock": bool(product.get("is_in_stock")),
        "image": image,
        "url": str(product.get("permalink") or ""),
        "description": _clean_html(
            product.get("short_description")
            or product.get("description")
        ),
        "categories": categories,
    }


def search(
    query: str,
    limit: int = 20,
    max_price: int | None = None,
    in_stock: bool = True,
) -> list[dict[str, Any]]:

    query = _normalize(query)

    if len(query) < 2:
        return []

    try:
        raw_products = _get_json({
            "search": query,
            "per_page": min(max(limit, 1), 100),
        })
    except CatalogError:
        raise

    results = []

    for raw in raw_products:
        product = _item(raw)

        if not product["id"] or not product["title"]:
            continue

        if in_stock and not product["in_stock"]:
            continue

        # Products with unknown prices cannot pass a budget filter.
        if max_price is not None:
            if product["final_price"] is None:
                continue

            if product["final_price"] > max_price:
                continue

        results.append(product)

    return results[:limit]


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
            in_stock=True,
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

    if not products:
        return []

    ids = []

    for product in products:
        try:
            ids.append(int(product["id"]))
        except (ValueError, TypeError, KeyError):
            continue

    if not ids:
        return []

    try:
        raw_products = _get_json({
            "include": ",".join(map(str, ids)),
            "per_page": min(len(ids), 100),
        })

        refreshed = [
            _item(raw)
            for raw in raw_products
        ]

        return [
            product
            for product in refreshed
            if product["id"] and product["in_stock"]
        ]

    except CatalogError:
        # Keep the selected products if the refresh endpoint temporarily fails.
        return [
            dict(product)
            for product in products
            if product.get("in_stock")
        ]


def showcase(
    limit: int = 50
) -> list[dict[str, Any]]:

    merged = []
    seen = set()

    for query in SHOWCASE_QUERIES:
        try:
            products = search(
                query,
                limit=20,
                in_stock=True,
            )
        except CatalogError:
            continue

        for product in products:
            product_id = product["id"]

            if product_id in seen:
                continue

            seen.add(product_id)
            merged.append(product)

            if len(merged) >= limit:
                return merged

    # If category searches return too few products, try the general catalog.
    if len(merged) < limit:
        try:
            raw_products = _get_json({
                "per_page": min(max(limit, 1), 100),
                "orderby": "popularity",
            })

            for raw in raw_products:
                product = _item(raw)

                if (
                    not product["id"]
                    or not product["in_stock"]
                    or product["id"] in seen
                ):
                    continue

                seen.add(product["id"])
                merged.append(product)

                if len(merged) >= limit:
                    break

        except CatalogError:
            pass

    return merged[:limit]
