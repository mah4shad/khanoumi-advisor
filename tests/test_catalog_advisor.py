import json
import unittest
from unittest import mock

import httpx

from app import advisor, khanoumi


def raw(pid, title, price, stock=True, discount=0):
    return {
        "id": pid, "slug": f"p-{pid}", "nameFa": title, "brand": {"nameFa": "برند"},
        "effectivePrice": price, "basePrice": price + (price // 10 if discount else 0),
        "discountPercent": discount, "isSalable": stock, "imageUrl": f"https://img/{pid}.jpg",
    }


class FakeStore:
    """Fake /api/ntl/v1/products. Records every request's query params."""

    def __init__(self, catalog):
        self.catalog = catalog  # query -> list of raw items
        self.calls: list[dict] = []
        self.fail_first = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append(params)
        if self.fail_first:
            self.fail_first -= 1
            return httpx.Response(503)
        items = self.catalog.get(params["query"], [])
        if "to_price" in params:
            items = [i for i in items if i["effectivePrice"] <= int(params["to_price"])]
        if params.get("has_stock") == "true":
            items = [i for i in items if i["isSalable"]]
        return httpx.Response(200, json={"isSuccess": True, "data": {"products": {"items": items}}})


def install_store(store: FakeStore):
    khanoumi._cache.clear()
    khanoumi._client = httpx.Client(base_url=khanoumi.BASE, transport=httpx.MockTransport(store.handler))


def llm_returns(*payloads):
    """Patch advisor.httpx.post to return the given JSON bodies as model content, in order."""
    queue = list(payloads)
    sent = []

    def post(url, json=None, headers=None, timeout=None):
        sent.append(json)
        body = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(body, int):
            return httpx.Response(body, text="err")
        content = body if isinstance(body, str) else __import__("json").dumps(body, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return mock.patch.object(advisor.httpx, "post", post), sent


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "k", "OPENROUTER_FALLBACK_MODELS": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_params_and_parsing(self):
        store = FakeStore({"ضد آفتاب": [raw("1", "ضد آفتاب الف", 400_000, discount=10)]})
        install_store(store)
        items = khanoumi.search("ضد آفتاب", limit=5, max_price=500_000)
        self.assertEqual(items[0]["final_price"], 400_000)
        self.assertEqual(items[0]["discount_pct"], 10)
        self.assertGreater(items[0]["price"], items[0]["final_price"])
        self.assertNotIn("sort", store.calls[0])  # no "Cheapest" sort
        self.assertEqual(store.calls[0]["to_price"], "500000")

    def test_retry_on_503(self):
        store = FakeStore({"q1": [raw("1", "الف", 100_000)]})
        store.fail_first = 1
        install_store(store)
        with mock.patch.object(khanoumi.time, "sleep"):
            self.assertEqual(len(khanoumi.search("q1")), 1)
        self.assertEqual(len(store.calls), 2)

    def test_collect_round_robin_and_dedupe(self):
        store = FakeStore({
            "AA": [raw(str(i), f"a{i}", 1000 * i) for i in range(1, 6)],
            "BB": [raw("1", "a1", 1000)] + [raw(f"b{i}", f"b{i}", 1000 * i) for i in range(1, 4)],
        })
        install_store(store)
        merged = khanoumi.collect(["AA", "BB"], limit=6)
        ids = [p["id"] for p in merged]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(any(i.startswith("b") for i in ids[:4]), ids)  # B not starved by A

    def test_collect_budget_enforced(self):
        store = FakeStore({"AA": [raw("1", "ارزان", 100_000), raw("2", "گران", 900_000)]})
        install_store(store)
        self.assertEqual([p["id"] for p in khanoumi.collect(["AA"], max_price=500_000)], ["1"])

    def test_refresh_live_and_stale(self):
        store = FakeStore({"محصول ۱": [raw("1", "محصول ۱", 123_000)]})
        install_store(store)
        old = [
            {"id": "1", "title": "محصول ۱", "final_price": 99_000, "in_stock": True},
            {"id": "9", "title": "ناپیدا", "final_price": 5, "in_stock": True},
        ]
        fresh = khanoumi.refresh(old)
        self.assertEqual(fresh[0]["final_price"], 123_000)
        self.assertTrue(fresh[1].get("stale"))


class AdvisorTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "k", "OPENROUTER_FALLBACK_MODELS": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = FakeStore({
            "ژل شستشو": [raw("1", "ژل ارزان", 200_000), raw("2", "ژل گران", 900_000)],
            "ضد آفتاب": [raw("3", "ضد آفتاب", 300_000)],
        })
        install_store(self.store)

    def test_chat_intent_skips_catalog(self):
        patch, _ = llm_returns({"intent": "chat", "queries": [], "answer": "سلام! چه کمکی از من برمی‌آید؟"})
        with patch:
            out = advisor.reply(advisor.new_session(), message="سلام", profile={})
        self.assertEqual(out["products"], [])
        self.assertEqual(self.store.calls, [])

    def test_recommend_enforces_budget_even_if_model_ignores_it(self):
        patch, sent = llm_returns(
            {"intent": "recommend", "queries": ["ژل شستشو", "ضد آفتاب"], "budget_toman": 500000},
            {"message": "این‌ها را ببین", "products": [
                {"id": "2", "role": "شوینده", "why": "x"}, {"id": "1", "role": "شوینده", "why": "y"},
                {"id": "999", "role": "جعلی", "why": "z"}],
             "routine": [{"time": "شب", "order": "۱", "title": "شستشو", "detail": "d", "product_id": "1"},
                         {"time": "صبح", "order": 1, "title": "ضدآفتاب", "detail": "d", "product_id": "404"}],
             "comparison": None},
        )
        with patch:
            out = advisor.reply(advisor.new_session(), message="ژل شستشو و ضد آفتاب زیر ۵۰۰ هزار تومان", profile={})
        self.assertEqual([p["id"] for p in out["products"]], ["1"])  # 900k and fake id removed
        self.assertEqual(out["routine"][0]["order"], 1)
        self.assertIsNone(out["routine"][1]["product_id"])
        self.assertTrue(all(c.get("to_price") == "500000" for c in self.store.calls))
        # catalog (not the whole user sentence) was sent as query
        self.assertEqual({c["query"] for c in self.store.calls}, {"ژل شستشو", "ضد آفتاب"})

    def test_profile_budget_beats_message(self):
        patch, _ = llm_returns({"intent": "recommend", "queries": ["ژل شستشو"]},
                               {"message": "ok", "products": [{"id": "1", "role": "r", "why": "w"}]})
        with patch:
            advisor.reply(advisor.new_session(), message="زیر ۹۰۰ هزار تومان ژل", profile={"budget": 250000})
        self.assertEqual(self.store.calls[0]["to_price"], "250000")

    def test_compare_uses_live_prices(self):
        session = advisor.new_session()
        session["products"] = {
            "1": {"id": "1", "title": "ژل ارزان", "final_price": 1, "in_stock": True, "brand": "", "discount_pct": 0},
            "3": {"id": "3", "title": "ضد آفتاب", "final_price": 2, "in_stock": True, "brand": "", "discount_pct": 0},
        }
        self.store.catalog["ژل ارزان"] = [raw("1", "ژل ارزان", 210_000)]
        self.store.catalog["ضد آفتاب"] = [raw("3", "ضد آفتاب", 310_000)]
        patch, sent = llm_returns({"message": "مقایسه", "products": [
            {"id": "1", "role": "a", "why": "w"}, {"id": "3", "role": "b", "why": "w"}],
            "comparison": {"summary": "s", "points": [{"id": "1", "pros": "p", "cons": "c"}, {"id": "3", "pros": "p", "cons": "c"}]}})
        with patch:
            out = advisor.reply(session, message="مقایسه کن", profile={}, compare_ids=["1", "3"])
        self.assertEqual({p["id"]: p["final_price"] for p in out["products"]}, {"1": 210_000, "3": 310_000})
        self.assertEqual(len(out["comparison"]["points"]), 2)
        self.assertEqual(len(sent), 1)  # planner skipped for explicit comparison

    def test_planner_failure_falls_back_to_heuristics(self):
        patch, _ = llm_returns("not json at all", {"message": "ok", "products": [{"id": "1", "role": "r", "why": "w"}]})
        self.store.catalog["مرطوب کننده چرب"] = []
        with patch:
            out = advisor.reply(advisor.new_session(), message="ژل شستشو", profile={"skin_type": "oily"})
        self.assertTrue(out["products"])

    def test_model_fallback_and_auth_errors(self):
        calls = []

        def post(url, json=None, headers=None, timeout=None):
            calls.append(json["model"])
            if json["model"] == "bad/model":
                return httpx.Response(503, text="down")
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"intent":"chat","answer":"سلام"}'}}]})

        env = {"OPENROUTER_MODEL": "bad/model", "OPENROUTER_FALLBACK_MODELS": "good/model"}
        with mock.patch.dict("os.environ", env), mock.patch.object(advisor.httpx, "post", post):
            out = advisor.reply(advisor.new_session(), message="سلام", profile={})
        self.assertEqual(calls, ["bad/model", "good/model"])
        self.assertEqual(out["message"], "سلام")

        patch, _ = llm_returns(401)
        with patch, self.assertRaises(advisor.AdvisorError):
            advisor._chat([{"role": "user", "content": "x"}])

    def test_missing_key(self):
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}):
            with self.assertRaises(advisor.AdvisorError):
                advisor.reply(advisor.new_session(), message="سلام", profile={})

    def test_empty_results_message(self):
        self.store.catalog = {}
        patch, _ = llm_returns({"intent": "recommend", "queries": ["چیزی نیست"]})
        with patch, self.assertRaises(advisor.AdvisorError):
            advisor.reply(advisor.new_session(), message="چیزی", profile={})


if __name__ == "__main__":
    unittest.main()
