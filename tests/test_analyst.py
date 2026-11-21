import unittest

from analysis.analyst import check


class FindingValidation(unittest.TestCase):
    def setUp(self):
        self.comments = {
            "c01": {"reader": "r1", "text": "Please show the sender."},
            "c02": {"reader": "r1", "text": "Same sender issue."},
            "c03": {"reader": "r2", "text": "Sender is obvious."},
        }
        self.theme = {
            "finding": "Wants the sender identified", "follow_up": "Would a header help?",
            "support": [{"id": "c01", "quote": "show the sender"},
                        {"id": "c02", "quote": "sender issue"}],
            "contrary": [{"id": "c03", "quote": "Sender is obvious"}],
        }

    def test_separate_readers_from_comment_count(self):
        result = check({"themes": [self.theme], "paired": []}, self.comments, {})
        finding = result["comment_findings"][0]
        self.assertEqual((finding["support_count"], finding["contrary_count"]), (1, 1))
        self.assertEqual(len(finding["support"]), 2)

    def test_wrong_quote_and_double_label_rejected(self):
        for ref in [{"id": "c03", "quote": "show the sender"},
                    {"id": "c02", "quote": "sender issue"}]:
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                check({"themes": [{**self.theme, "contrary": [ref]}], "paired": []}, self.comments, {})

    def test_unobserved_transition_rejected(self):
        with self.assertRaises(ValueError):
            check({"themes": [], "paired": [{"key": "invented"}]}, self.comments, {})
