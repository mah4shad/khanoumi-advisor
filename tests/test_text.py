import unittest

from app.text import normalize, parse_budget, wants_comparison


class TextTests(unittest.TestCase):
    def test_normalize_digits(self):
        self.assertEqual(normalize("۷۰۰٬۰۰۰ تومان"), "700000 تومان")
        self.assertEqual(normalize("ك ي"), "ک ی")

    def test_budget_cases(self):
        cases = {
            "ضدآفتاب روزمره برای پوست مختلط، زیر ۵۰۰ هزار تومان": 500_000,
            "بودجه ام ۷۰۰۰۰۰ تومنه": 700_000,
            "تا 1.5 میلیون": 1_500_000,
            "یک میلیون تومان": 1_000_000,
            "۲ میلیون و ۵۰۰ هزار تومان": 2_500_000,
            "حدود ۲۰۰,۰۰۰ تومان": 200_000,
        }
        for text, expected in cases.items():
            self.assertEqual(parse_budget(text), expected, text)

    def test_budget_not_confused(self):
        for text in ["من ۳۰ سالمه", "پوستم چرب است", "یک کرم می‌خواهم", "ضد آفتاب spf 50", ""]:
            self.assertIsNone(parse_budget(text), text)

    def test_compare(self):
        self.assertTrue(wants_comparison("فرق این دو چیه؟"))
        self.assertFalse(wants_comparison("یک مرطوب کننده می‌خواهم"))


if __name__ == "__main__":
    unittest.main()
