
"""Beauty advisor using Rozalin Beauty products and Google Gemini."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any

import httpx

from .khanoumi import CatalogError, collect, refresh
from .text import normalize, parse_budget, wants_comparison


log = logging.getLogger("advisor")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
DEFAULT_MODEL = "gemini-3.5-flash-lite"

SKIN_TYPES = {
    "dry": "خشک",
    "oily": "چرب",
    "combination": "مختلط",
    "normal": "معمولی",
    "sensitive": "حساس",
}

CONCERNS = {
    "acne": ("جوش و آکنه", "ضد جوش صورت"),
    "dryness": ("خشکی", "کرم پوست خشک"),
    "pigmentation": ("لک و تیرگی", "کرم ضد لک صورت"),
    "sunscreen": ("محافظت از آفتاب", "ضد آفتاب صورت"),
    "aging": ("چین و چروک", "سرم ضد چروک"),
    "hydration": ("کم‌آبی", "سرم هیالورونیک اسید"),
    "pores": ("منافذ", "سرم نیاسینامید"),
    "cleansing": ("پاکسازی", "ژل شستشو صورت"),
}


PLANNER = """تو برنامه‌ریز مشاور زیبایی فروشگاه رزالین هستی.
پیام مشتری، اطلاعات پوست و پیام‌های قبلی را بررسی کن.

خروجی فقط یک JSON معتبر باشد:
{
  "intent": "chat | recommend | routine | compare",
  "queries": [],
  "budget_toman": null,
  "answer": ""
}

قواعد:
- chat برای سلام، تشکر، گپ و سوال عمومی است که پیشنهاد محصول لازم ندارد.
- recommend برای درخواست پیشنهاد محصول است.
- routine برای درخواست روتین صبح و شب است.
- compare برای مقایسه محصولات است.
- سوال عمومی مراقبت پوست را بدون اجبار به جستجوی محصول جواب بده.
- اگر مشتری نوع پوست یا نیازش را گفته و پیشنهاد محصول یا روتین خواسته، recommend یا routine انتخاب کن.
- برای درخواست محصول، حداکثر ۴ عبارت کوتاه و مشخص فارسی بساز.
- عبارت جستجو باید نام دسته محصول و در صورت امکان نوع پوست را داشته باشد.
- برای روتین، دسته‌های متفاوت مثل شوینده، مرطوب‌کننده و ضدآفتاب را در نظر بگیر.
- کل جمله مشتری را به عنوان عبارت جستجو کپی نکن.
- بودجه را به تومان و به صورت عدد کامل بنویس.
- اگر بودجه مشخص نیست، null بنویس.
- برای سوال عمومی، answer را کوتاه و مفید به فارسی بنویس.
- تشخیص پزشکی نده.
"""


SYSTEM = """تو مشاور زیبایی فروشگاه اینترنتی رزالین هستی.

فقط فارسی، صمیمی و روشن جواب بده.
تشخیص پزشکی نده و برای مشکلات پوستی جدی یا ماندگار، مراجعه به پزشک را پیشنهاد کن.

فقط از فهرست محصولات همین پیام محصول پیشنهاد بده.
هیچ محصول، قیمت، تخفیف، موجودی یا لینکی را از خودت نساز.
قیمت دقیق را در متن تکرار نکن؛ رابط کاربری قیمت زنده را نمایش می‌دهد.

اگر بودجه مشخص شده، محصول گران‌تر از بودجه را انتخاب نکن.
محصول بدون قیمت مشخص را در پیشنهاد دارای بودجه انتخاب نکن.
حداکثر ۴ محصول انتخاب کن.
برای هر محصول نقش مشخص و دلیل کوتاه بنویس.
دو محصول هم‌نقش را مگر برای مقایسه با هم انتخاب نکن.

روتین را کوتاه و عملی بنویس:
- صبح و شب را جدا کن.
- ترتیب استفاده را رعایت کن.
- اگر مشتری روتین نخواسته، routine می‌تواند خالی باشد.

اگر compare_ids داده شده یا کاربر مقایسه خواسته، comparison را پر کن و فقط همان محصولات را مقایسه کن.
در غیر این صورت comparison را null بگذار.

ادعای درمانی قطعی نکن.
فقط درباره کاربرد احتمالی محصول بر اساس نام، برند و اطلاعات موجود صحبت کن.

خروجی فقط یک JSON معتبر باشد و متن اضافه نداشته باشد:
{
  "message": "پاسخ برای مشتری",
  "products": [
    {
      "id": "شناسه محصول از فهرست",
      "role": "نقش محصول",
      "why": "دلیل انتخاب در یک جمله"
    }
  ],
  "routine": [
    {
      "time": "صبح یا شب",
      "order": 1,
      "title": "عنوان گام",
      "detail": "توضیح کوتاه",
      "product_id": "شناسه یا null"
    }
  ],
  "comparison": null
}

ساختار comparison:
{
  "summary": "جمع‌بندی",
  "points": [
    {
      "id": "شناسه",
      "pros": "مزیت",
      "cons": "محدودیت"
    }
  ]
}
"""


class AdvisorError(RuntimeError):
    pass


def new_session() -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "history": [],
        "products": {},
    }


# ---------------------------------------------------------------------------
# Main reply


def reply(
    session: dict[str, Any],
    *,
    message: str,
    profile: dict[str, Any],
    compare_ids: list[str] | None = None,
) -> dict[str, Any]:

    message = " ".join((message or "").split())

    if len(message) < 2:
        raise AdvisorError("پیام کوتاه است. سوالت را کمی کامل‌تر بنویس.")

    session.setdefault("history", [])
    session.setdefault("products", {})

    wanted = [
        pid
        for pid in dict.fromkeys(
            str(i).strip() for i in (compare_ids or [])
        )
        if pid
    ][:4]

    selected = [
        session["products"][pid]
        for pid in wanted
        if pid in session["products"]
    ]

    budget = _budget(profile.get("budget")) or parse_budget(message)

    plan = _plan(
        session,
        message,
        profile,
        budget,
        has_selection=len(selected) >= 2,
    )

    budget = (
        _budget(profile.get("budget"))
        or budget
        or _budget(plan.get("budget_toman"))
    )

    # General conversation does not require a product search.
    if plan["intent"] == "chat" and not selected:
        text = plan.get("answer") or _offline_chat_answer(message)
        _remember(session, message, text, [])
        return _result(session, text, [], [], None, 0)

    comparing = len(selected) >= 2

    if comparing:
        try:
            catalog = refresh(selected)
        except CatalogError:
            catalog = selected
    else:
        try:
            catalog = collect(
                plan["queries"],
                limit=50,
                max_price=budget,
            )
        except CatalogError:
            log.exception("Rozalin catalog search failed")
            catalog = []

    if not catalog:
        text = _no_product_answer(budget, message)
        _remember(session, message, text, [])
        return _result(session, text, [], [], None, 0)

    by_id = {
        p["id"]: p
        for p in catalog
        if p.get("id")
    }

    # Gemini may be unavailable. In that case, use real catalog products
    # and create a safe local response instead of returning an error.
    try:
        raw = _complete(
            session,
            message=message,
            profile=profile,
            budget=budget,
            catalog=catalog,
            compare_ids=wanted if comparing else [],
        )
        parsed = _parse(raw)

    except AdvisorError:
        log.exception("Gemini response failed; using local fallback")
        return _fallback_reply(
            session=session,
            message=message,
            catalog=catalog,
            profile=profile,
            budget=budget,
            comparing=comparing,
        )

    chosen = _attach(parsed.get("products") or [], by_id)

    if not comparing:
        chosen = [
            p for p in chosen
            if p["in_stock"]
            and (
                budget is None
                or (
                    p["final_price"] is not None
                    and p["final_price"] <= budget
                )
            )
        ]

    if not chosen:
        chosen = _fallback_products(catalog, budget, message)

    routine = _routine(parsed.get("routine"), by_id)

    comparison = (
        _comparison(parsed.get("comparison"), by_id)
        if (
            comparing
            or wants_comparison(message)
            or parsed.get("comparison")
        )
        else None
    )

    text = (
        str(parsed.get("message") or "").strip()
        or "چند محصول موجود رزالین را با توجه به نیازت جدا کردم."
    )

    _remember(session, message, text, chosen)
    _save_products(session, catalog)

    return _result(
        session,
        text,
        chosen,
        routine,
        comparison,
        len(catalog),
    )


def _result(
    session,
    text,
    products,
    routine,
    comparison,
    catalog_size,
) -> dict[str, Any]:

    return {
        "session_id": session["id"],
        "message": text,
        "products": products,
        "routine": routine,
        "comparison": comparison,
        "catalog_size": catalog_size,
    }


def _remember(
    session: dict[str, Any],
    message: str,
    text: str,
    chosen: list[dict[str, Any]],
) -> None:

    note = text

    if chosen:
        note += "\n[محصولات پیشنهادی: " + "؛ ".join(
            p["title"] for p in chosen
        ) + "]"

    session["history"].append({
        "role": "user",
        "content": message,
    })

    session["history"].append({
        "role": "assistant",
        "content": note,
    })

    session["history"] = session["history"][-8:]


def _save_products(session, products):
    for product in products:
        if not product.get("id"):
            continue

        session["products"][product["id"]] = {
            k: v
            for k, v in product.items()
            if k not in {"role", "why", "stale"}
        }

    session["products"] = dict(
        list(session["products"].items())[-120:]
    )


# ---------------------------------------------------------------------------
# Offline fallback


def _is_small_talk(message: str) -> bool:
    text = normalize(message)

    phrases = [
        "سلام",
        "درود",
        "صبح بخیر",
        "شب بخیر",
        "خسته نباشی",
        "ممنون",
        "مرسی",
        "سپاس",
        "خداحافظ",
        "خوبی",
        "چطوری",
    ]

    return any(text == normalize(p) for p in phrases)


def _offline_chat_answer(message: str) -> str:
    text = normalize(message)

    if _is_small_talk(message):
        if "ممنون" in text or "مرسی" in text or "سپاس" in text:
            return "خواهش می‌کنم 🌷 هر سوالی درباره مراقبت پوست یا محصولات داشتی بپرس."

        if "خداحافظ" in text:
            return "خدانگهدار 🌷 امیدوارم روز خوبی داشته باشی."

        return "سلام! خوش اومدی 🌷 درباره نوع پوست، مراقبت روزانه یا انتخاب محصولات آرایشی و بهداشتی هر سوالی داری بپرس."

    if "روتین" in text:
        return (
            "برای یک روتین ساده، معمولاً صبح از شوینده ملایم، "
            "مرطوب‌کننده و ضدآفتاب استفاده می‌شود. "
            "شب هم شوینده و مرطوب‌کننده کافی است. "
            "برای پیشنهاد دقیق‌تر، نوع پوستت را هم در نظر می‌گیریم."
        )

    if "چرب" in text and ("جوش" in text or "آکنه" in text):
        return (
            "برای پوست چرب و مستعد جوش، شوینده ملایم، "
            "مرطوب‌کننده سبک و ضدآفتاب غیرکومدون‌زا انتخاب‌های مناسبی هستند. "
            "شست‌وشوی بیش از حد و استفاده هم‌زمان از چند محصول لایه‌بردار می‌تواند تحریک‌کننده باشد."
        )

    if "ضد آفتاب" in text or "ضدآفتاب" in text:
        return (
            "ضدآفتاب با محافظت طیف گسترده و SPF حداقل ۳۰ انتخاب مناسبی برای استفاده روزانه است. "
            "مقدار کافی استفاده کن و در فضای باز تمدیدش کن."
        )

    return (
        "حتماً راهنمایی‌ات می‌کنم. اگر سوالت درباره مراقبت پوست است، "
        "نوع پوست و دغدغه‌ات را بگو تا دقیق‌تر راهنمایی‌ات کنم."
    )


def _no_product_answer(budget, message: str) -> str:
    if budget:
        return (
            "در حال حاضر محصول موجود و دارای قیمت مشخصی متناسب با این نیاز "
            "و بودجه در رزالین پیدا نکردم. می‌توانی بودجه را کمی تغییر بدهی "
            "یا نوع محصول موردنظرت را دقیق‌تر بنویسی."
        )

    return (
        "در حال حاضر محصول موجود و مرتبطی در رزالین پیدا نکردم. "
        "می‌توانی نام دسته محصول را دقیق‌تر بنویسی؛ مثلاً شوینده پوست چرب، "
        "مرطوب‌کننده یا ضدآفتاب."
    )


def _fallback_products(catalog, budget, message):
    products = []

    for product in catalog:
        if not product.get("in_stock"):
            continue

        price = product.get("final_price")

        if budget is not None:
            if price is None or price > budget:
                continue

        products.append({
            **product,
            "role": "پیشنهاد محصول",
            "why": "این محصول از محصولات موجود رزالین و مرتبط با جستجوی توست.",
        })

        if len(products) >= 3:
            break

    return products


def _fallback_reply(
    *,
    session,
    message,
    catalog,
    profile,
    budget,
    comparing,
):
    chosen = _fallback_products(catalog, budget, message)

    if not chosen:
        text = _no_product_answer(budget, message)
        _remember(session, message, text, [])
        return _result(session, text, [], [], None, len(catalog))

    text = (
        "با توجه به درخواستت این محصولات موجود رزالین را پیدا کردم. "
        "فعلاً پاسخ‌گوی هوشمند در دسترس نیست، اما می‌توانی محصولات را بررسی کنی."
    )

    _remember(session, message, text, chosen)
    _save_products(session, catalog)

    return _result(
        session,
        text,
        chosen,
        [],
        None,
        len(catalog),
    )


# ---------------------------------------------------------------------------
# Planner


def _plan(
    session,
    message,
    profile,
    budget,
    *,
    has_selection: bool,
) -> dict[str, Any]:

    heuristic = {
        "intent": (
            "compare"
            if has_selection or wants_comparison(message)
            else _offline_intent(message)
        ),
        "queries": _queries(profile, message),
        "budget_toman": budget,
        "answer": "",
    }

    if has_selection or os.getenv("USE_PLANNER", "1") == "0":
        return heuristic

    payload = {
        "message": message,
        "skin_type": SKIN_TYPES.get(
            str(profile.get("skin_type") or ""),
            "نامشخص",
        ),
        "concerns": _concern_names(profile),
        "budget_toman": budget,
    }

    messages = [{"role": "system", "content": PLANNER}]

    messages += [
        {
            "role": item["role"],
            "content": item["content"],
        }
        for item in session["history"][-4:]
    ]

    messages.append({
        "role": "user",
        "content": json.dumps(payload, ensure_ascii=False),
    })

    try:
        data = _parse(
            _chat(
                messages,
                temperature=0.1,
                max_tokens=500,
            )
        )

    except AdvisorError as exc:
        log.warning("Planner unavailable; using local rules: %s", exc)
        return heuristic

    intent = str(data.get("intent") or "").strip().lower()

    if intent not in {"chat", "recommend", "routine", "compare"}:
        intent = heuristic["intent"]

    queries = [
        normalize(q)[:80]
        for q in (data.get("queries") or [])
        if isinstance(q, str)
    ]

    queries = [
        q for q in dict.fromkeys(queries)
        if len(q) >= 2
    ][:4]

    if intent != "chat" and not queries:
        queries = heuristic["queries"]

    answer = str(data.get("answer") or "").strip()

    if intent == "chat" and not answer:
        answer = _offline_chat_answer(message)

    return {
        "intent": intent,
        "queries": queries,
        "budget_toman": data.get("budget_toman"),
        "answer": answer,
    }


def _offline_intent(message: str) -> str:
    text = normalize(message)

    if _is_small_talk(message):
        return "chat"

    if wants_comparison(message):
        return "compare"

    if "روتین" in text or "برنامه مراقبت" in text:
        return "routine"

    product_words = [
        "محصول",
        "پیشنهاد",
        "بخرم",
        "خرید",
        "معرفی کن",
        "چی بگیرم",
        "چه کرمی",
        "چه سرمی",
        "شوینده",
        "ضد آفتاب",
        "ضدآفتاب",
        "مرطوب کننده",
        "مرطوب‌کننده",
        "آبرسان",
        "ضد جوش",
        "ضد لک",
    ]

    if any(word in text for word in product_words):
        return "recommend"

    skin_words = [
        "پوستم",
        "پوست چرب",
        "پوست خشک",
        "جوش",
        "آکنه",
        "لک",
        "تیرگی",
        "منافذ",
        "خشکی",
        "کم آبی",
        "کم‌آبی",
    ]

    if any(word in text for word in skin_words):
        return "recommend"

    return "chat"


# ---------------------------------------------------------------------------
# Query building


def _queries(profile: dict[str, Any], message: str) -> list[str]:
    text = normalize(message)

    skin = SKIN_TYPES.get(
        str(profile.get("skin_type") or ""),
        "",
    )

    if not skin:
        if "چرب" in text:
            skin = "چرب"
        elif "خشک" in text:
            skin = "خشک"
        elif "مختلط" in text:
            skin = "مختلط"
        elif "حساس" in text:
            skin = "حساس"

    queries = []

    concerns = profile.get("concerns") or []

    if isinstance(concerns, str):
        concerns = [concerns]

    for key in concerns:
        pair = CONCERNS.get(str(key))

        if pair:
            queries.append(f"{pair[1]} {skin}".strip())

    keyword_queries = [
        (("جوش", "آکنه"), "ضد جوش صورت"),
        (("لک", "تیرگی", "جای جوش"), "کرم ضد لک صورت"),
        (("ضد آفتاب", "ضدآفتاب", "آفتاب"), "ضد آفتاب صورت"),
        (("خشک", "کم آبی", "کم‌آبی"), "مرطوب کننده صورت"),
        (("چین و چروک", "ضد چروک"), "سرم ضد چروک"),
        (("منافذ", "نیاسینامید"), "سرم نیاسینامید"),
        (("شوینده", "پاک کننده", "پاک‌کننده"), "شوینده صورت"),
        (("مرطوب کننده", "مرطوب‌کننده", "آبرسان"), "مرطوب کننده صورت"),
        (("سرم",), "سرم صورت"),
    ]

    for keywords, query in keyword_queries:
        if any(word in text for word in keywords):
            queries.append(f"{query} {skin}".strip())

    if "روتین" in text:
        queries.extend([
            f"شوینده صورت {skin}".strip(),
            f"مرطوب کننده صورت {skin}".strip(),
            f"ضد آفتاب صورت {skin}".strip(),
        ])

    if not queries:
        queries.extend([
            f"مرطوب کننده صورت {skin}".strip(),
            f"شوینده صورت {skin}".strip(),
            "ضد آفتاب صورت",
        ])

    return list(dict.fromkeys(
        " ".join(q.split())
        for q in queries
        if len(q.strip()) >= 2
    ))[:4]


def _concern_names(profile: dict[str, Any]) -> list[str]:
    raw = profile.get("concerns") or []

    if isinstance(raw, str):
        raw = [raw]

    return [
        CONCERNS[str(i)][0]
        if str(i) in CONCERNS
        else str(i)
        for i in raw
    ]


def _budget(value: Any) -> int | None:
    if value is None or value == "":
        return None

    try:
        amount = int(float(normalize(str(value))))
    except (TypeError, ValueError):
        return None

    return amount if amount > 0 else None


# ---------------------------------------------------------------------------
# Gemini


def _api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()

    if not key:
        raise AdvisorError(
            "کلید Google AI Studio تنظیم نشده است."
        )

    return key


def _models() -> list[str]:
    model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip()
    return [model or DEFAULT_MODEL]


def _chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.4,
    max_tokens: int = 1800,
) -> str:

    key = _api_key()

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    model = _models()[0]

    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }

    for attempt in range(2):
        try:
            response = httpx.post(
                GEMINI_URL,
                json=payload,
                headers=headers,
                timeout=60,
                proxy=os.getenv("GEMINI_PROXY") or None,
            )

        except httpx.HTTPError as exc:
            raise AdvisorError(
                f"خطا در اتصال به Google AI Studio: {exc.__class__.__name__}"
            ) from exc

        if response.status_code == 400 and "response_format" in payload:
            payload.pop("response_format")
            continue

        if response.status_code in (401, 403):
            raise AdvisorError(
                f"خطای دسترسی Google ({response.status_code})."
            )

        if response.status_code == 429:
            raise AdvisorError(
                "محدودیت درخواست Google AI Studio."
            )

        if response.status_code >= 500:
            raise AdvisorError(
                f"سرویس Google موقتاً در دسترس نیست ({response.status_code})."
            )

        if response.status_code >= 400:
            raise AdvisorError(
                f"خطای Google AI Studio ({response.status_code})."
            )

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]

        except (ValueError, KeyError, IndexError, TypeError):
            raise AdvisorError(
                "پاسخ Google AI Studio قابل خواندن نیست."
            ) from None

        if isinstance(content, list):
            content = "".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict)
            )

        if isinstance(content, str) and content.strip():
            return content

        raise AdvisorError(
            "Google AI Studio پاسخ متنی برنگرداند."
        )

    raise AdvisorError(
        "دریافت پاسخ از Google AI Studio ناموفق بود."
    )


def _complete(
    session: dict[str, Any],
    *,
    message: str,
    profile: dict[str, Any],
    budget: int | None,
    catalog: list[dict[str, Any]],
    compare_ids: list[str],
) -> str:

    compact = [
        {
            "id": p["id"],
            "title": p["title"],
            "brand": p["brand"],
            "final_price": p["final_price"],
            "discount_pct": p["discount_pct"],
            "in_stock": p["in_stock"],
        }
        for p in catalog
    ]

    user = {
        "skin_type": SKIN_TYPES.get(
            str(profile.get("skin_type") or ""),
            "نامشخص",
        ),
        "concerns": _concern_names(profile),
        "budget_toman": budget,
        "compare_ids": compare_ids,
        "message": message,
        "products": compact,
    }

    messages = [{"role": "system", "content": SYSTEM}]

    messages += [
        {
            "role": item["role"],
            "content": item["content"],
        }
        for item in session["history"][-6:]
    ]

    messages.append({
        "role": "user",
        "content": json.dumps(user, ensure_ascii=False),
    })

    return _chat(messages)


# ---------------------------------------------------------------------------
# JSON parsing


def _parse(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    try:
        data = json.loads(text)

    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)

        if not match:
            raise AdvisorError(
                "مشاور پاسخ ساخت‌یافته نداد."
            ) from None

        try:
            data = json.loads(match.group(0))

        except json.JSONDecodeError:
            raise AdvisorError(
                "مشاور پاسخ ساخت‌یافته نداد."
            ) from None

    if not isinstance(data, dict):
        raise AdvisorError("پاسخ مشاور نامعتبر بود.")

    return data


# ---------------------------------------------------------------------------
# Result shaping


def _attach(
    picks: list[Any],
    by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:

    chosen = []
    seen = set()

    for pick in picks:
        if not isinstance(pick, dict):
            continue

        pid = str(pick.get("id") or "")
        product = by_id.get(pid)

        if not product or pid in seen:
            continue

        if not product.get("in_stock"):
            continue

        seen.add(pid)

        chosen.append({
            **product,
            "role": str(
                pick.get("role") or "پیشنهاد"
            ).strip()[:40],
            "why": str(
                pick.get("why") or ""
            ).strip()[:180],
        })

        if len(chosen) == 4:
            break

    return chosen


def _routine(
    raw: Any,
    by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:

    routine = []

    for step in raw or []:
        if not isinstance(step, dict):
            continue

        pid = str(step.get("product_id") or "") or None

        if pid not in by_id:
            pid = None

        try:
            order = int(float(normalize(str(step.get("order")))))

        except (TypeError, ValueError):
            order = len(routine) + 1

        routine.append({
            "time": (
                "شب"
                if "شب" in str(step.get("time") or "")
                else "صبح"
            ),
            "order": order,
            "title": str(
                step.get("title") or "گام مراقبت"
            ).strip()[:80],
            "detail": str(
                step.get("detail") or ""
            ).strip()[:240],
            "product_id": pid,
        })

    return routine[:12]


def _comparison(
    raw: Any,
    by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:

    if not isinstance(raw, dict):
        return None

    points = []

    for point in raw.get("points") or []:
        if not isinstance(point, dict):
            continue

        pid = str(point.get("id") or "")

        if pid not in by_id:
            continue

        points.append({
            "id": pid,
            "title": by_id[pid]["title"],
            "pros": str(
                point.get("pros") or ""
            ).strip()[:180],
            "cons": str(
                point.get("cons") or ""
            ).strip()[:180],
        })

    if len(points) < 2:
        return None

    return {
        "summary": str(
            raw.get("summary") or ""
        ).strip()[:300],
        "points": points[:4],
    }
