import unittest

from src.app import greet


class GreetTest(unittest.TestCase):
    def test_greet(self):
        self.assertEqual(greet("world"), "hello, world")


if __name__ == "__main__":
    unittest.main()
