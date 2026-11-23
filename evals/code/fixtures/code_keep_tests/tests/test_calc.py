import unittest

from src.calc import apply_discount


class ApplyDiscountTest(unittest.TestCase):
    def test_quarter_off(self):
        self.assertAlmostEqual(apply_discount(200, 25), 150.0)

    def test_no_discount(self):
        self.assertAlmostEqual(apply_discount(80, 0), 80.0)

    def test_half_off(self):
        self.assertAlmostEqual(apply_discount(99.9, 50), 49.95)


if __name__ == "__main__":
    unittest.main()
