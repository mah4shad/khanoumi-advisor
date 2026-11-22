"""Run on your own machine:  python scripts/check_setup.py

Checks, in order: .env key, OpenRouter, Khanoumi catalog API, and one full advisor reply.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")


def step(title: str) -> None:
    print(f"\n== {title}")


def main() -> int:
    failed = False

    step("1) کلید OpenRouter")
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        print("✗ OPENROUTER_API_KEY در .env خالی است")
        return 1
    try:
        r = httpx.get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        if r.status_code == 200:
            data = r.json().get("data", {})
            print(f"✓ کلید معتبر است. مصرف: {data.get('usage')}  سقف: {data.get('limit')}")
        else:
            print(f"✗ OpenRouter کد {r.status_code} داد: {r.text[:150]}")
            failed = True
    except httpx.HTTPError as exc:
        print(f"✗ اتصال به OpenRouter برقرار نشد: {exc}")
        failed = True

    step("2) API کاتالوگ خانومی")
    from app import khanoumi

    try:
        items = khanoumi.search("ضد آفتاب صورت", limit=5)
        if not items:
            print("✗ پاسخ آمد ولی محصولی نبود (ساختار JSON عوض شده؟)")
            failed = True
        for p in items[:3]:
            print(f"✓ {p['title'][:50]} | {p['brand']} | {p['final_price']} | موجود={p['in_stock']}")
        if items:
            print("  ← قیمت بالا باید به «تومان» باشد. اگر ۱۰ برابر است KHANOUMI_PRICE_DIVISOR=10 بگذارید.")
            missing = [k for k in ("image", "url", "title") if not items[0].get(k)]
            if missing:
                print(f"! فیلدهای خالی: {missing}")
        capped = khanoumi.search("ضد آفتاب صورت", limit=10, max_price=300_000)
        over = [p for p in capped if p["final_price"] and p["final_price"] > 300_000]
        print(f"✓ فیلتر بودجه ۳۰۰٬۰۰۰ تومان: {len(capped)} محصول، {len(over)} مورد گران‌تر (باید ۰ باشد)")
        failed |= bool(over)
    except khanoumi.CatalogError as exc:
        print(f"✗ {exc}")
        failed = True

    step("3) یک پاسخ کامل مشاور")
    if failed:
        print("چون مرحله قبل خطا داشت رد شد.")
        return 1
    from app.advisor import new_session, reply

    out = reply(
        new_session(),
        message="پوست چرب و جوشی دارم، روتین ساده زیر ۵۰۰ هزار تومان می‌خواهم",
        profile={"skin_type": "oily", "concerns": ["acne"], "budget": None},
    )
    print("پیام:", out["message"])
    for p in out["products"]:
        print(f"  • {p['role']}: {p['title'][:50]} — {p['final_price']}")
    print(f"روتین: {len(out['routine'])} گام | بررسی‌شده از {out['catalog_size']} محصول")
    print("\n✓ همه چیز کار می‌کند" if out["products"] else "\n✗ محصولی برنگشت")
    return 0 if out["products"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
