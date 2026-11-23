import unittest

from src.pagination import page_slice


class PageSliceTest(unittest.TestCase):
    def test_first_page(self):
        self.assertEqual(page_slice([1, 2, 3, 4, 5], 1, 2), [1, 2])

    def test_second_page(self):
        self.assertEqual(page_slice([1, 2, 3, 4, 5], 2, 2), [3, 4])

    def test_last_partial_page(self):
        self.assertEqual(page_slice([1, 2, 3, 4, 5], 3, 2), [5])

    def test_beyond_range(self):
        self.assertEqual(page_slice([1, 2, 3], 5, 2), [])


if __name__ == "__main__":
    unittest.main()
