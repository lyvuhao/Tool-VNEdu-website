"""Nhiều câu nhận xét cho một rule (nhanxet.rules / nhanxet.write_plan)."""

import collections
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nhanxet.models import CommentRule  # noqa: E402
from nhanxet.rules import compile_comment_rules, split_comment_variants  # noqa: E402
from nhanxet.write_plan import build_comment_write_rows_from_live_data  # noqa: E402


def live_rows(values, comments=None):
    comments = comments or [""] * len(values)
    return [
        {"rowIndex": i, "studentCode": f"HS{i}", "studentName": f"HS {i}", "sourceValue": value,
         "currentComment": comment, "commentInputName": f"c{i}"}
        for i, (value, comment) in enumerate(zip(values, comments))
    ]


def plan(rows, rules, overwrite=False):
    return build_comment_write_rows_from_live_data(rows, "k", "n", compile_comment_rules(rules), overwrite)


class CommentVariantTests(unittest.TestCase):
    RULES = [CommentRule(">=8", "A1 | A2 | A3"), CommentRule("<8", "B1|B2")]

    def test_split(self):
        self.assertEqual(split_comment_variants("X || | Y "), ["X", "Y"])
        self.assertEqual(split_comment_variants("Một câu"), ["Một câu"])

    def test_single_variant_unchanged(self):
        rules = [CommentRule(">=8", "Tốt"), CommentRule("<8", "Khá")]
        out = plan(live_rows(["9", "5", "", "7"], ["", "Cũ", "", "Tốt"]), rules)
        self.assertEqual([(r.proposed_comment, r.status) for r in out], [
            ("Tốt", "ready"), ("", "skip_existing_comment"), ("", "skip_no_score"), ("", "skip_existing_comment"),
        ])
        self.assertEqual(out[0].reason, "Khớp rule `>=8`.")
        # Cho phép ghi đè: nhận xét tay bị thay, điểm 7 -> "Khá"
        self.assertEqual(plan(live_rows(["7"], ["Tốt"]), rules, overwrite=True)[0].proposed_comment, "Khá")

    def test_round_robin_and_deterministic(self):
        rows = live_rows(["9"] * 7)
        got = [r.proposed_comment for r in plan(rows, self.RULES)]
        self.assertEqual(got, ["A1", "A2", "A3", "A1", "A2", "A3", "A1"])
        self.assertEqual([r.proposed_comment for r in plan(rows, self.RULES)], got)

    def test_rules_are_independent(self):
        got = [r.proposed_comment for r in plan(live_rows(["9", "5", "9", "5", "5"]), self.RULES)]
        self.assertEqual(got, ["A1", "B1", "A2", "B2", "B1"])

    def test_existing_variant_kept_and_counted(self):
        comments = ["A1", "A1", "", "", "", "A2", "", "", "", ""]
        out = plan(live_rows(["9"] * 10, comments), self.RULES, overwrite=True)
        self.assertTrue(all(out[i].status == "skip_same" for i in (0, 1, 5)))
        counts = collections.Counter(r.proposed_comment for r in out)
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    def test_manual_comment_not_overwritten(self):
        out = plan(live_rows(["9", "9"], ["Nhận xét tay", ""]), self.RULES)
        self.assertEqual(out[0].status, "skip_existing_comment")


if __name__ == "__main__":
    unittest.main()
