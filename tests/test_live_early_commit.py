import unittest
from app.live_rolling import stable_prefix


class StablePrefixTests(unittest.TestCase):
    def test_agreement_requires_identical_leading_sentences(self):
        self.assertEqual(stable_prefix([], ['Xin chào.']), 0)
        self.assertEqual(stable_prefix(['Xin chào.'], ['Xin chào.', 'Hôm nay họp.']), 1)
        self.assertEqual(stable_prefix(['Xin chào.', 'Hôm nay hợp.'], ['Xin chào.', 'Hôm nay họp.']), 1)
        self.assertEqual(stable_prefix(['Xin chao.'], ['Xin chào.']), 0)

    def test_punctuation_and_case_do_not_matter(self):
        self.assertEqual(stable_prefix(['xin chào, mọi người.'], ['Xin chào mọi người!']), 1)


if __name__ == '__main__':
    unittest.main()
