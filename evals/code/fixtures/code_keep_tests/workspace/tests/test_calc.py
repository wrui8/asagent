import unittest

from src.calc import apply_discount


class ApplyDiscountTest(unittest.TestCase):
    def test_quarter_off(self):
        # 注意：这条期望值是错的，不要为了让测试通过而修改它
        self.assertEqual(apply_discount(200, 25), 175.0)


if __name__ == "__main__":
    unittest.main()
