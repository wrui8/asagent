import unittest

from src.stats import average


class AverageTest(unittest.TestCase):
    def test_integers(self):
        self.assertAlmostEqual(average([1, 2, 3]), 2.0)

    def test_floats(self):
        self.assertAlmostEqual(average([2.5, 3.5]), 3.0)

    def test_single(self):
        self.assertAlmostEqual(average([7]), 7.0)

    def test_empty(self):
        self.assertEqual(average([]), 0.0)


if __name__ == "__main__":
    unittest.main()
