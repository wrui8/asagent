import unittest

from src.textutil import slugify


class SlugifyTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")

    def test_extra_spaces(self):
        self.assertEqual(slugify("  Multiple   Spaces  "), "multiple-spaces")

    def test_symbols_and_digits(self):
        self.assertEqual(slugify("Python 3.12 Release Notes"), "python-3-12-release-notes")


if __name__ == "__main__":
    unittest.main()
