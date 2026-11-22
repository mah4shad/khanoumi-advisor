"""Read-only client for the public Khanoumi product catalog."""

from __future__ import annotations
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

BASE = "https://www.khanoumi.com"
PRODUCTS = "/api/ntl/v1/products"
SHOWCASE_QUERIES = (
    "کرم مرطوب کننده صورت",
    "ضد آفتاب صورت",
    "سرم آبرسان صورت",
    "ژل شستشو صورت",
    "کرم دور چشم",
)
CACHE_TTL = 180
SHOWCASE_TTL = 600
CACHE_MAX = 400

_lock = threading.Lock()
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_client = httpx.Client(
    base_url=BASE,
    timeout=40,
    follow_redirects=True,
    trust_env=False,
    headers={
        "Accept": "application/json, text/html;q=0.9",
        "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
        "Referer": f"{BASE}/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    },
)


class CatalogError(RuntimeError):
    pass


def _divisor() -> int:
    """If the API ever returns rial instead of toman, set KHANOUMI_PRICE_DIVISOR=10."""
    try:
        return max(1, int(os.getenv("KHANOUMI_PRICE_DIVISOR", "1")))
    except ValueError:
        return 1


def _cache_get(key: str) -> list[dict[str, Any]] | None:
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
    return None


def _cache_put(key: str, ttl: int, value: list[dict[str, Any]]) -> None:
    now = time.monotonic()
    with _lock:
        if len(_cache) >= CACHE_MAX:
            for stale in [k for k, (exp, _) in _cache.items() if exp <= now]:
                _cache.pop(stale, None)
            while len(_cache) >= CACHE_MAX:
                _cache.pop(next(iter(_cache)))
        _cache[key] = (now + ttl, value)



def _get_json(params: dict[str, Any]) -> dict[str, Any]:
    last: Exception | None = None

    for attempt in range(2):
        try:
            response = _client.get(PRODUCTS, params=params)

            print("KHANOUMI STATUS:", response.status_code)
            print("KHANOUMI CONTENT TYPE:", response.headers.get("content-type"))
            print("KHANOUMI RESPONSE PREVIEW:", response.text[:500])

            if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(0.7)
                continue

            response.raise_for_status()
            body = response.json()

            if isinstance(body, dict):
                return body

            last = ValueError(f"Unexpected response type: {type(body)}")

        except (httpx.HTTPError, ValueError) as exc:
            last = exc
            print("KHANOUMI ERROR:", repr(exc))

        if attempt == 0:
            time.sleep(0.7)

    raise CatalogError(
        "اتصال به فروشگاه خانومی برقرار نشد. چند لحظه بعد دوباره تلاش کنید."
    ) from last



def search(
    query: str,
    *,
    limit: int = 20,
    max_price: int | None = None,
    in_stock: bool = True,
) -> list[dict[str, Any]]:
    query = " ".join(query.split())
    if len(query) < 2:
        return []
    key = f"{query}|{limit}|{max_price}|{in_stock}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    # Note: no "Cheapest" sort. It floods results with the cheapest, often unrelated,
    # items. The price ceiling is enforced by to_price and again client-side.
    params: dict[str, Any] = {
        "query": query,
        "page_number": 1,
        "page_size": min(max(limit, 1), 60),
        "has_stock": "true" if in_stock else None,
        "to_price": (max_price * _divisor()) if max_price else None,
    }
    params = {k: v for k, v in params.items() if v is not None}
    body = _get_json(params)
    if not body.get("isSuccess"):
        raise CatalogError("فروشگاه خانومی پاسخ قابل استفاده نداد.")

    items = ((body.get("data") or {}).get("products") or {}).get("items") or []
    products = [_item(raw) for raw in items if isinstance(raw, dict) and not raw.get("ad")]
    products = [p for p in products if p["id"] and p["title"]]
    _cache_put(key, CACHE_TTL, products)
    return products


def collect(queries: list[str], *, limit: int = 50, max_price: int | None = None) -> list[dict[str, Any]]:
    """Run searches in parallel and merge round-robin, so every query is represented."""
    queries = list(dict.fromkeys(q for q in queries if q and len(q.strip()) >= 2))
    if not queries:
        return []
    per_query = max(8, min(20, limit))
    batches: list[list[dict[str, Any]]] = []
    errors: list[CatalogError] = []

    def run(q: str) -> list[dict[str, Any]]:
        return search(q, limit=per_query, max_price=max_price, in_stock=True)

    with ThreadPoolExecutor(max_workers=min(4, len(queries))) as pool:
        futures = [(q, pool.submit(run, q)) for q in queries]
    for q, future in futures:
        try:
            batches.append([{**p, "matched_query": q} for p in future.result()])
        except CatalogError as exc:
            errors.append(exc)
    if not any(batches) and errors:
        raise errors[0]

    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    depth = max((len(b) for b in batches), default=0)
    for i in range(depth):
        for batch in batches:
            if i >= len(batch):
                continue
            product = batch[i]
            if product["id"] in seen:
                continue
            if max_price and product["final_price"] and product["final_price"] > max_price:
                continue
            seen.add(product["id"])
            merged.append(product)
            if len(merged) >= limit:
                return merged
    return merged


def refresh(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-read live price/stock for products seen earlier in the session.

    The public API has no lookup-by-id here, so each product is found again by its title.
    If it cannot be found, the old copy is returned and flagged stale.
    """

    def one(product: dict[str, Any]) -> dict[str, Any]:
        try:
            for fresh in search(product["title"], limit=10, in_stock=False):
                if fresh["id"] == product["id"]:
                    return fresh
        except CatalogError:
            pass
        return {**product, "stale": True}

    if not products:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(products))) as pool:
        return list(pool.map(one, products))


def showcase(limit: int = 50) -> list[dict[str, Any]]:
    key = f"showcase|{limit}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    products = collect(list(SHOWCASE_QUERIES), limit=limit)
    _cache_put(key, SHOWCASE_TTL, products)
    return products


def _item(raw: dict[str, Any]) -> dict[str, Any]:
    brand = raw.get("brand") or {}
    div = _divisor()
    final = _money(raw.get("effectivePrice"), div)
    base = _money(raw.get("basePrice"), div)
    discount = _int(raw.get("discountPercent"))
    slug = raw.get("slug") or ""
    return {
        "id": str(raw.get("id") or ""),
        "slug": slug,
        "title": (raw.get("nameFa") or "").strip(),
        "brand": brand.get("nameFa") or brand.get("nameEn") or "",
        "final_price": final,
        "price": base if discount else final,
        "discount_pct": discount,
        "in_stock": bool(raw.get("isSalable")),
        "image": raw.get("imageUrl") or raw.get("mainImageUrl") or "",
        "url": f"{BASE}/products/{slug}" if slug else BASE,
    }


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _money(value: Any, divisor: int = 1) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value) // divisor
    except (TypeError, ValueError):
        return None
