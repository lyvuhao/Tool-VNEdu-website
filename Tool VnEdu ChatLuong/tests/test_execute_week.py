"""PlanExecutor._execute_week (auto_khbd/engine/executor/week_execution.py + week_fill.py).

Executor giả theo kịch bản: client/page giả ghi lại lệnh, không cần Chrome.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_khbd.engine.executor import js as JS  # noqa: E402
from auto_khbd.engine.executor import week_execution as WE  # noqa: E402
from auto_khbd.engine.executor import week_fill as WF  # noqa: E402

JS_NAMES = {
    JS._JS_SET_SLOT_DROPDOWNS: "lop",
    JS._JS_SET_MON: "mon",
    JS._JS_WAIT_PHAN_MON_OPTION: "wait_pm",
    JS._JS_SET_PHAN_MON: "pm",
    JS._JS_EXT_SET_PHAN_MON_VALUE: "pm_ext",
    JS._JS_GET_KHOI_FOR_LOP: "khoi",
    JS._JS_FETCH_TEN_BAI: "ten_bai",
    JS._JS_SET_PPCT_TEN_BAI: "ppct",
    JS._JS_CLOSE_BENIGN_DIALOG: "close_dialog",
}


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def op(i, **kw):
    base = dict(tuan=5, row_key=f"2_s_{i}", lop_id="L1", lop_text="6A1", mon_id="M1", mon_text="Toán",
                phan_mon_id="", phan_mon_text="", ppct=10 + i, ten_bai="", skip=False, ghi_chu="", trang_thai="0")
    base.update(kw)
    return Obj(**base)


def week(ops, pre_action=""):
    return Obj(tuan=5, skip_reason="", strategy=Obj(value="fill"), pre_action=pre_action, fill_ops=ops)


class _Stop:
    def __init__(self, stop=False):
        self.stop = stop

    def is_set(self):
        return self.stop


class _Page:
    def __init__(self, ex):
        self.ex = ex

    def evaluate(self, script, arg=None):
        name = JS_NAMES.get(script, "block_autofill")
        self.ex.calls.append((name, arg))
        return self.ex.page_results.get(name, lambda a: None)(arg)


class _Client:
    def __init__(self, ex):
        self.ex = ex
        self.page = _Page(ex)

    def enable_edit_mode(self): self.ex.calls.append(("edit_mode",))
    def reload_table_and_wait(self, **kw): self.ex.calls.append(("reload",))
    def reset_blocked_autofill_log(self): pass
    def gen_from_prev_week(self, tuan):
        self.ex.calls.append(("gen_prev", tuan))
        return Obj(ok=True, data={"success": True})
    def gen_from_tkb(self, tuan): return Obj(ok=True, data={})
    def save_week_via_button(self, wait_s=0):
        self.ex.calls.append(("save",))
        return self.ex.save_result


class _Log:
    def __init__(self):
        self.entries, self.saved, self.removed = [], 0, []

    def add(self, entry): self.entries.append(entry)
    def save(self): self.saved += 1
    def remove(self, tuan, row_key): self.removed.append((tuan, row_key))
    def all_entries(self): return list(self.entries)


class FakeExecutor(WE.WeekExecutionMixin, WF.WeekFillMixin):
    def __init__(self, **cfg):
        self.calls, self.events = [], []
        self.client = _Client(self)
        self.page_results = {"ten_bai": lambda a: {"ok": True, "ten_bai": f"Bài {a['ppct']}"}}
        self.save_result = Obj(ok=True, success=True, msg="", errors=[])
        self.stop_event = _Stop(cfg.get("stop", False))
        self._report = Obj(extras_detected_count=0, weeks_already_complete=0)
        self.ten_bai_fallback = cfg.get("ten_bai_fallback", False)
        self._fallback_count = 0
        self.fallback_log = _Log()
        self._session_id = "s1"
        self.wait_after_pre_action_s = self.wait_after_set_fields_s = self.wait_after_save_s = 0
        self.fast_safe_mode = cfg.get("fast_safe", False)
        self.verify_after_save = cfg.get("verify_after_save", True)
        self._ctx = Obj(my_token="tok", my_user_id="u", nam_hoc=2025)
        self.web_week = cfg.get("web_week", 5)
        self.scan = cfg.get("scan")
        self.dom_ok = cfg.get("dom_ok", True)
        self.saved_ok = cfg.get("saved_ok", True)

    def _emit(self, kind, **kw): self.events.append((kind, kw.get("message", "")))
    def _switch_and_verify_week(self, tuan): return self.web_week
    def _scan_and_diff_week(self, wp):
        if self.scan:
            return self.scan(wp)
        return False, [o for o in wp.fill_ops if not o.skip], [], {}
    def _apply_inflight_ppct_shift(self, **kw): return 0
    def _interruptible_sleep(self, s): pass
    def _disable_autofill_blocker_best_effort(self): self.calls.append(("disable_blocker",))
    def _fill_hdtn_title_from_word_catalog(self, op, tuan): return False
    def _write_ppct_fields_for_ops(self, ops, failed): self.calls.append(("write_ppct", sorted(failed)))
    def _verify_fill_ops_dom(self, ops, failed): return self.dom_ok, ([] if self.dom_ok else ["2_s_1: PPCT 9 != 11"])
    def _read_current_week(self): return 5
    def _emit_blocker_stats(self, tuan, label=""): pass
    def _verify_week_after_save(self, tuan, ops): return self.saved_ok, ([] if self.saved_ok else ["  • 2_s_1 lệch"])
    def _cleanup_stale_log_entries_for_week(self, tuan): self.calls.append(("cleanup", tuan))

    def names(self):
        return [c[0] for c in self.calls]

    def args_of(self, name, key):
        """Giá trị `key` trong tham số của các lệnh JS `name` đã chạy."""
        return [c[1][key] for c in self.calls if c[0] == name]

    def kinds(self):
        return [k for k, _ in self.events]


def execute(ex, wp, dry_run=False):
    with mock.patch.object(WE.time, "sleep", lambda s: None), mock.patch.object(WF.time, "sleep", lambda s: None):
        return ex._execute_week(wp, dry_run)


class ExecuteWeekTests(unittest.TestCase):
    def test_happy_path_phases_in_order(self):
        ex = FakeExecutor()
        wp = week([op(1), op(2, phan_mon_id="P1", phan_mon_text="Đại số")])
        ex.page_results["wait_pm"] = lambda a: {"ok": True}
        ex.page_results["pm"] = lambda a: {"ok": True}
        wr = execute(ex, wp)
        self.assertTrue(wr.save_ok)
        names = ex.names()
        order = [names.index(n) for n in ("lop", "mon", "pm", "ten_bai", "ppct", "close_dialog", "write_ppct", "save")]
        self.assertEqual(order, sorted(order))
        self.assertEqual([o.ten_bai for o in wp.fill_ops], ["Bài 11", "Bài 12"])
        self.assertEqual(ex.events[-1][0], "week_done")
        self.assertTrue(ex.events[-1][1].startswith("ok=True errors=0 took="))

    def test_wrong_week_stops_before_filling(self):
        ex = FakeExecutor(web_week=4)
        wr = execute(ex, week([op(1)]))
        self.assertFalse(wr.save_ok)
        self.assertNotIn("lop", ex.names())
        self.assertNotIn("save", ex.names())
        self.assertIn("disable_blocker", ex.names())

    def test_week_already_complete_is_skipped(self):
        ex = FakeExecutor(scan=lambda wp: (True, [], [], {}))
        wr = execute(ex, week([op(1)]))
        self.assertTrue(wr.skipped and wr.save_ok)
        self.assertEqual(ex._report.weeks_already_complete, 1)
        self.assertNotIn("save", ex.names())

    def test_partial_supplement_marks_matching_ops_skip(self):
        ex = FakeExecutor(scan=lambda wp: (False, [wp.fill_ops[1]], [], {}))
        wp = week([op(1), op(2)])
        execute(ex, wp)
        self.assertEqual([o.skip for o in wp.fill_ops], [True, False])
        self.assertEqual(ex.args_of("lop", "rk"), ["2_s_2"])
        self.assertIn("week_partial_supplement", ex.kinds())

    def test_failed_lop_skips_later_phases_for_that_op(self):
        ex = FakeExecutor()

        def set_lop(arg):
            if arg["rk"] == "2_s_1":
                raise RuntimeError("không có lớp")
        ex.page_results["lop"] = set_lop
        execute(ex, week([op(1), op(2)]))
        self.assertEqual(ex.args_of("mon", "rk"), ["2_s_2"])
        self.assertEqual(ex.args_of("ppct", "rk"), ["2_s_2"])
        self.assertIn(("write_ppct", ["2_s_1"]), ex.calls)

    def test_fast_mode_dom_mismatch_does_not_save(self):
        ex = FakeExecutor(fast_safe=True, dom_ok=False)
        wr = execute(ex, week([op(1)]))
        self.assertFalse(wr.save_ok)
        self.assertEqual(wr.save_msg, "Chế độ nhanh hủy lưu vì DOM vẫn lệch trước khi nhấn Lưu.")
        self.assertNotIn("save", ex.names())
        self.assertEqual(ex.names().count("write_ppct"), 2)

    def test_stop_before_save(self):
        ex = FakeExecutor(stop=True)
        wr = execute(ex, week([op(1)]))
        self.assertEqual((wr.save_ok, wr.save_msg), (False, "Đã dừng trước khi lưu"))
        self.assertNotIn("save", ex.names())

    def test_stop_before_pre_action(self):
        ex = FakeExecutor(stop=True)
        wr = execute(ex, week([op(1)], pre_action="gen_prev"))
        self.assertNotIn("gen_prev", ex.names())
        self.assertIn("stop", ex.kinds())
        self.assertTrue(wr.save_ok)

    def test_ten_bai_fallback_logged_then_rolled_back_when_save_fails(self):
        ex = FakeExecutor(ten_bai_fallback=True)
        ex.page_results["ten_bai"] = lambda a: {"ok": True, "ten_bai": ""}
        ex.save_result = Obj(ok=True, success=False, msg="Chưa chọn lớp", errors=[])
        wr = execute(ex, week([op(1)]))
        self.assertFalse(wr.save_ok)
        self.assertEqual(ex.args_of("ppct", "ten_bai"), [" "])
        self.assertEqual(ex._fallback_count, 1)
        self.assertEqual(len(ex.fallback_log.entries), 1)
        self.assertEqual(ex.fallback_log.removed, [(5, "2_s_1")])
        self.assertEqual(ex._week_pending_log_entries, [])

    def test_verify_after_save_mismatch_fails_week(self):
        ex = FakeExecutor(saved_ok=False)
        wr = execute(ex, week([op(1)]))
        self.assertFalse(wr.save_ok)
        self.assertIn("kiểm chứng lại thấy 1 ô lệch", wr.save_msg)

    def test_dry_run_touches_nothing(self):
        ex = FakeExecutor()
        wr = execute(ex, week([op(1)], pre_action="gen_prev"), dry_run=True)
        self.assertTrue(wr.save_ok)
        self.assertEqual(ex.calls, [])
        self.assertEqual(ex.kinds(), ["week_start", "week_done"])


if __name__ == "__main__":
    unittest.main()
