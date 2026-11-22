"""Beauty advisor: live Khanoumi products plus a Google Gemini model."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any

import httpx

from .khanoumi import collect, refresh
from .text import normalize, parse_budget, wants_comparison

log = logging.getLogger("advisor")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
DEFAULT_MODEL = "gemini-2.5-flash"

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

PLANNER = """تو بخش برنامه‌ریز مشاور زیبایی فروشگاه خانومی هستی. پیام مشتری، پرونده پوستش و چند پیام قبلی را می‌خوانی و تصمیم می‌گیری.
خروجی فقط یک JSON معتبر باشد:
{
  "intent": "chat | recommend | routine | compare",
  "queries": ["۱ تا ۴ عبارت کوتاه فارسی برای جستجوی محصول در فروشگاه"],
  "budget_toman": null,
  "answer": ""
}
قواعد:
- chat: سلام، تشکر، گپ، یا سوال عمومی مراقبت پوست که به پیشنهاد محصول نیاز ندارد. در این حالت answer را کوتاه و صمیمی و فارسی بنویس و queries خالی باشد. تشخیص پزشکی نده.
- recommend: مشتری محصول می‌خواهد. routine: روتین صبح و شب می‌خواهد. compare: مقایسه می‌خواهد.
- هر query باید نام یک دسته محصول به‌همراه نوع پوست یا نیاز باشد، مثل «ژل شستشوی صورت پوست چرب» یا «ضد آفتاب بی‌رنگ پوست مختلط». کل جمله مشتری را کپی نکن.
- برای روتین کامل، چند دسته مختلف بده: شوینده، مرطوب‌کننده، ضدآفتاب و در صورت نیاز سرم.
- اگر مشتری بودجه‌ای به تومان گفته، budget_toman را به عدد کامل بده (۵۰۰ هزار تومان = 500000)، وگرنه null.
- اگر پیام به محصول قبلی اشاره دارد («ارزون‌ترش»، «اولی»)، از پیام‌های قبلی برای ساختن query کمک بگیر.
"""

SYSTEM = """تو مشاور زیبایی فروشگاه اینترنتی خانومی هستی.
فقط فارسی، صمیمی و کوتاه جواب بده. تشخیص پزشکی نده و اگر مشکل پوستی جدی است به پزشک ارجاع بده.
فقط از فهرست محصولات همین پیام پیشنهاد بده و هیچ محصولی بیرون از فهرست نام نبر. قیمت، تخفیف و موجودی را خودت نساز؛ در متن هم قیمت دقیق را تکرار نکن چون رابط کاربری قیمت زنده را نشان می‌دهد.
اگر بودجه آمده، محصول گران‌تر از بودجه را انتخاب نکن.
حداکثر ۴ محصول انتخاب کن و برای هر کدام نقش مشخص در روتین بگذار. دو محصول هم‌نقش را مگر برای مقایسه با هم انتخاب نکن.
روتین را عملی و کوتاه بنویس: صبح و شب، به ترتیب استفاده. اگر مشتری روتین نخواسته، routine می‌تواند خالی باشد.
اگر compare_ids داده شده یا کاربر مقایسه خواسته، comparison را حتماً پر کن و فقط همان محصولات را مقایسه کن؛ در غیر این صورت null بگذار.
ادعای درمانی قطعی نکن (مثلاً «لک را کامل از بین می‌برد»). فقط درباره مواد و کاربرد محصول بر اساس نام و برند در فهرست حرف بزن و اگر اطلاعات کافی نیست صادقانه بگو.
خروجی فقط یک JSON معتبر باشد، بدون متن اضافه:
{
  "message": "پاسخ برای مشتری",
  "products": [{"id": "شناسه از فهرست", "role": "نقش محصول", "why": "دلیل انتخاب در یک جمله"}],
  "routine": [{"time": "صبح یا شب", "order": 1, "title": "عنوان گام", "detail": "توضیح کوتاه", "product_id": "شناسه یا null"}],
  "comparison": null
}
شکل comparison وقتی لازم است:
{"summary": "جمع‌بندی", "points": [{"id": "شناسه", "pros": "مزیت", "cons": "محدودیت"}]}
"""


class AdvisorError(RuntimeError):
    pass


def new_session() -> dict[str, Any]:
    return {"id": uuid.uuid4().hex, "history": [], "products": {}}


# --------------------------------------------------------------------------- main


def reply(
    session: dict[str, Any],
    *,
    message: str,
    profile: dict[str, Any],
    compare_ids: list[str] | None = None,
) -> dict[str, Any]:
    message = " ".join((message or "").split())
    if len(message) < 2:
        raise AdvisorError("پیام کوتاه است. نیاز پوست یا سوالت را بنویس.")
    _api_key()  # fail early with a clear message

    wanted = [pid for pid in dict.fromkeys(str(i).strip() for i in (compare_ids or [])) if pid][:4]
    selected = [session["products"][pid] for pid in wanted if pid in session["products"]]

    budget = _budget(profile.get("budget")) or parse_budget(message)
    plan = _plan(session, message, profile, budget, has_selection=len(selected) >= 2)
    budget = _budget(profile.get("budget")) or budget or _budget(plan.get("budget_toman"))

    # Small talk and general questions: answer directly, no catalog lookup.
    if plan["intent"] == "chat" and not selected:
        text = plan["answer"] or "سلام! نوع پوست، نیاز و بودجه‌ات را بگو تا از محصولات موجود خانومی پیشنهاد بدهم."
        _remember(session, message, text, [])
        return _result(session, text, [], [], None, 0)

    comparing = len(selected) >= 2
    if comparing:
        # Prices and stock must be live, so re-read the products the customer picked.
        catalog = refresh(selected)
    else:
        catalog = collect(plan["queries"], limit=50, max_price=budget)
        if not catalog:
            raise AdvisorError("با این بودجه و نیاز، محصول موجودی در خانومی پیدا نشد. بودجه یا عبارت جستجو را عوض کن.")
    by_id = {p["id"]: p for p in catalog}

    raw = _complete(session, message=message, profile=profile, budget=budget, catalog=catalog, compare_ids=wanted if comparing else [])
    parsed = _parse(raw)

    chosen = _attach(parsed.get("products") or [], by_id)
    if not comparing:
        # The model is told the budget, but never rely on it: enforce it here.
        chosen = [p for p in chosen if p["in_stock"] and (not budget or not p["final_price"] or p["final_price"] <= budget)]
    if not chosen:
        fallback = next((p for p in catalog if p["in_stock"]), catalog[0])
        chosen = [{**fallback, "role": "پیشنهاد نزدیک", "why": "نزدیک‌ترین محصول موجود به درخواست تو در فروشگاه."}]

    routine = _routine(parsed.get("routine"), by_id)
    comparison = _comparison(parsed.get("comparison"), by_id) if (comparing or wants_comparison(message) or parsed.get("comparison")) else None
    text = str(parsed.get("message") or "").strip() or "چند محصول موجود فروشگاه را با توجه به پوست و بودجه‌ات جدا کردم."

    _remember(session, message, text, chosen)
    for product in by_id.values():
        session["products"][product["id"]] = {k: v for k, v in product.items() if k not in {"role", "why", "stale"}}
    session["products"] = dict(list(session["products"].items())[-120:])

    return _result(session, text, chosen, routine, comparison, len(catalog))


def _result(session, text, products, routine, comparison, catalog_size) -> dict[str, Any]:
    return {
        "session_id": session["id"],
        "message": text,
        "products": products,
        "routine": routine,
        "comparison": comparison,
        "catalog_size": catalog_size,
    }


def _remember(session: dict[str, Any], message: str, text: str, chosen: list[dict[str, Any]]) -> None:
    note = text
    if chosen:
        note += "\n[محصولات پیشنهادی: " + "؛ ".join(p["title"] for p in chosen) + "]"
    session["history"].append({"role": "user", "content": message})
    session["history"].append({"role": "assistant", "content": note})
    session["history"] = session["history"][-8:]


# ------------------------------------------------------------------------ planner


def _plan(session, message, profile, budget, *, has_selection: bool) -> dict[str, Any]:
    heuristic = {
        "intent": "compare" if (has_selection or wants_comparison(message)) else "recommend",
        "queries": _queries(profile, message),
        "budget_toman": budget,
        "answer": "",
    }
    if has_selection or os.getenv("USE_PLANNER", "1") == "0":
        return heuristic

    payload = {
        "message": message,
        "skin_type": SKIN_TYPES.get(str(profile.get("skin_type") or ""), "نامشخص"),
        "concerns": _concern_names(profile),
        "budget_toman": budget,
    }
    messages = [{"role": "system", "content": PLANNER}]
    messages += [{"role": t["role"], "content": t["content"]} for t in session["history"][-4:]]
    messages.append({"role": "user", "content": json.dumps(payload, ensure_ascii=False)})
    try:
        data = _parse(_chat(messages, temperature=0.1, max_tokens=500))
    except AdvisorError as exc:
        log.warning("planner failed, using heuristics: %s", exc)
        return heuristic

    intent = str(data.get("intent") or "").strip().lower()
    if intent not in {"chat", "recommend", "routine", "compare"}:
        intent = heuristic["intent"]
    queries = [normalize(q)[:80] for q in (data.get("queries") or []) if isinstance(q, str)]
    queries = [q for q in dict.fromkeys(queries) if len(q) >= 2][:4]
    if intent != "chat" and not queries:
        queries = heuristic["queries"]
    answer = str(data.get("answer") or "").strip()
    if intent == "chat" and not answer:
        intent = "recommend"
        queries = heuristic["queries"]
    return {"intent": intent, "queries": queries, "budget_toman": data.get("budget_toman"), "answer": answer}


def _queries(profile: dict[str, Any], message: str) -> list[str]:
    """Fallback query builder, used when the planner model is unavailable."""
    skin = SKIN_TYPES.get(str(profile.get("skin_type") or ""), "")
    queries: list[str] = []
    concerns = profile.get("concerns") or []
    if isinstance(concerns, str):
        concerns = [concerns]
    for key in concerns:
        pair = CONCERNS.get(str(key))
        if pair:
            queries.append(f"{pair[1]} {skin}".strip())
    if skin:
        queries.append(f"مرطوب کننده {skin}")
        queries.append(f"شوینده صورت {skin}")
    elif not queries:
        queries.append("مرطوب کننده صورت")
    queries.append(message[:60])
    unique = [q for q in dict.fromkeys(" ".join(q.split()) for q in queries) if len(q) >= 2]
    return unique[:4]


def _concern_names(profile: dict[str, Any]) -> list[str]:
    raw = profile.get("concerns") or []
    if isinstance(raw, str):
        raw = [raw]
    return [CONCERNS[str(i)][0] if str(i) in CONCERNS else str(i) for i in raw]


def _budget(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        amount = int(float(normalize(str(value))))
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


# ---------------------------------------------------------------------- LLM calls

def _api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise AdvisorError("کلید Google AI Studio در فایل .env تنظیم نشده.")
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
                f"خطای Google ({response.status_code}): {response.text[:500]}"
            )

        if response.status_code == 429:
            raise AdvisorError("محدودیت درخواست Google AI Studio؛ کمی بعد دوباره امتحان کن.")

        if response.status_code >= 400:
            raise AdvisorError(
                f"خطای Google AI Studio ({response.status_code}): {response.text[:300]}"
            )

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise AdvisorError("پاسخ Google AI Studio معتبر یا قابل خواندن نیست.") from None

        if isinstance(content, list):
            content = "".join(
                item.get("text", "") for item in content if isinstance(item, dict)
            )

        if isinstance(content, str) and content.strip():
            return content

        raise AdvisorError("Google AI Studio پاسخ متنی برنگرداند.")

    raise AdvisorError("دریافت پاسخ از Google AI Studio ناموفق بود.")

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
        "skin_type": SKIN_TYPES.get(str(profile.get("skin_type") or ""), "نامشخص"),
        "concerns": _concern_names(profile),
        "budget_toman": budget,
        "compare_ids": compare_ids,
        "message": message,
        "products": compact,
    }
    messages = [{"role": "system", "content": SYSTEM}]
    messages += [{"role": t["role"], "content": t["content"]} for t in session["history"][-6:]]
    messages.append({"role": "user", "content": json.dumps(user, ensure_ascii=False)})
    return _chat(messages)


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
            raise AdvisorError("مشاور پاسخ ساخت‌یافته نداد. یک بار دیگر بپرس.") from None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            raise AdvisorError("مشاور پاسخ ساخت‌یافته نداد. یک بار دیگر بپرس.") from None
    if not isinstance(data, dict):
        raise AdvisorError("پاسخ مشاور نامعتبر بود.")
    return data


# ----------------------------------------------------------------- result shaping


def _attach(picks: list[Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pick in picks:
        if not isinstance(pick, dict):
            continue
        pid = str(pick.get("id") or "")
        product = by_id.get(pid)
        if not product or pid in seen:
            continue
        seen.add(pid)
        chosen.append(
            {
                **product,
                "role": str(pick.get("role") or "پیشنهاد").strip()[:40],
                "why": str(pick.get("why") or "").strip()[:180],
            }
        )
        if len(chosen) == 4:
            break
    return chosen


def _routine(raw: Any, by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    routine: list[dict[str, Any]] = []
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
        routine.append(
            {
                "time": "شب" if "شب" in str(step.get("time") or "") else "صبح",
                "order": order,
                "title": str(step.get("title") or "گام مراقبت").strip()[:80],
                "detail": str(step.get("detail") or "").strip()[:240],
                "product_id": pid,
            }
        )
    return routine[:12]


def _comparison(raw: Any, by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    points = []
    for point in raw.get("points") or []:
        if not isinstance(point, dict):
            continue
        pid = str(point.get("id") or "")
        if pid not in by_id:
            continue
        points.append(
            {
                "id": pid,
                "title": by_id[pid]["title"],
                "pros": str(point.get("pros") or "").strip()[:180],
                "cons": str(point.get("cons") or "").strip()[:180],
            }
        )
    if len(points) < 2:
        return None
    return {"summary": str(raw.get("summary") or "").strip()[:300], "points": points[:4]}
