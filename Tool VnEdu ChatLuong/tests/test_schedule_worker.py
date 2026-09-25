"""Worker nhập Sổ đầu bài theo lịch — chế độ thủ công (auto_sdb/app/schedule_job.py).

Dùng ChromeBridge giả theo kịch bản — không cần Chrome/tkinter.
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auto_sdb.app.schedule_job as J  # noqa: E402

CLOSED = "Target page, context or browser has been closed"

SLOTS = [
    {"thu": 2, "buoi": "Sáng", "tiet": 1},
    {"thu": 2, "buoi": "Sáng", "tiet": 2},
    {"thu": 8, "buoi": "Chiều", "tiet": 3},
]


def table_row(i, slot, **extra):
    thu = "CN\n14/09" if str(slot["thu"]) == "8" else f"{slot['thu']}\n08/09"
    base = {"thu": thu, "buoi": slot["buoi"], "tiet": str(slot["tiet"]), "has_data": False, "has_add_btn": True,
            "add_btn_index": i, "rowIdx": i + 1, "ppct": ""}
    base.update(extra)
    return base


class FakeBridge:
    """ChromeBridge giả: `script` quy định kết quả từng thao tác."""

    script = {}

    def __init__(self, port=None):
        self.calls = []
        self.week = None
        FakeBridge.last = self

    def _seq(self, key, default):
        value = self.script.get(key)
        if isinstance(value, list) and value:
            return value.pop(0)
        return default

    def connect(self): return True, "ok"
    def setup_dialog_auto_accept(self): pass
    def select_dropdown(self, label, text):
        self.calls.append(("select", label, text))
        if label == "tuan":
            self.week = int(text.split()[-1])
        return self._seq("select_seq", (True, "ok"))
    def read_table(self):
        self.calls.append(("read_table", self.week))
        rows = self.script.get("rows_by_week", {}).get(self.week)
        if rows is None:
            rows = [table_row(i, s) for i, s in enumerate(SLOTS)]
        return self._seq("read_seq", (True, copy.deepcopy(rows)))
    def click_add_button(self, row_index=-1, row_dom_index=-1):
        self.calls.append(("click", self.week, row_index, row_dom_index))
        return self._seq("click_seq", (True, "clicked"))
    def wait_for_lesson_form(self, **k): return self._seq("form_seq", (True, "open"))
    def fill_form(self, **k):
        self.calls.append(("fill", self.week, k["ppct"]))
        return self._seq("fill_seq", (True, "filled"))
    def wait_for_form_ready_to_save(self, **k): pass
    def save_form_auto(self, buoi_hoc=""):
        self.calls.append(("save", self.week))
        return self._seq("save_seq", (True, "saved", {}))
    def wait_for_slot_data_fetch(self, *a, **k): return self._seq("fetch_seq", (False, "no data", None))
    def wait_for_slot_data(self, *a, **k): return False, "no data", None
    def close_form(self): self.calls.append(("close",))
    def wait_for_lesson_form_closed(self, **k): pass
    def cleanup_after_automation(self, restore_view_mode=True): return True, "clean"
    def disconnect(self): pass


class _Queue:
    def __init__(self): self.items = []
    def put(self, item): self.items.append(copy.deepcopy(item))


class _Stop:
    def __init__(self, after=None): self.after, self.n = after, 0
    def is_set(self):
        self.n += 1
        return self.after is not None and self.n > self.after


class _App:
    def __init__(self, stop_after=None):
        self._schedule_queue = _Queue()
        self._schedule_stop_event = _Stop(stop_after)
        self._schedule_stop_reason = ""

    def _format_schedule_slot_label(self, thu, buoi, tiet):
        return f"Thứ {thu} {buoi} Tiết {tiet}"

    @staticmethod
    def _extract_existing_ppct_value(row):
        digits = "".join(ch for ch in str(row.get("ppct", "")) if ch.isdigit())
        return int(digits) if digits else None


def run_job(script=None, params=None, stop_after=None):
    FakeBridge.script = copy.deepcopy(script or {})
    J.ChromeBridge = FakeBridge
    app = _App(stop_after)
    base = {"slots": SLOTS, "tuan_from": 1, "tuan_to": 2, "lop": "6A1", "port": 9224, "ppct_start": 10,
            "hs_nghi": "0", "diem": "", "nhan_xet_raw": "Tốt"}
    base.update(params or {})
    J.ScheduleJob(app, base).run()
    events = app._schedule_queue.items
    done = events[-1][1]
    statuses = [e[1]["status"] for e in events if e[0] == "slot_result"]
    return app, events, done, statuses


class SlotKeyTests(unittest.TestCase):
    def test_sunday_and_accents_match(self):
        self.assertEqual(J.slot_key(8, "Chiều", 3), J.slot_key("CN\n14/09", "chieu", " 3 "))
        self.assertNotEqual(J.slot_key(2, "Sáng", 1), J.slot_key(3, "Sáng", 1))

    def test_week_cache_mark_saved(self):
        cache = J.WeekRowCache()
        cache.set_rows([table_row(0, SLOTS[0])])
        cache.mark_saved(SLOTS[0], 12, mon_hoc_text="Toán", phan_mon_text="Đại số")
        self.assertEqual(cache.get(SLOTS[0])["ppct"], "12")
        self.assertEqual(cache.get(SLOTS[0])["mon_hoc"], "Toán (Đại số)")
        cache.mark_saved(SLOTS[0], 13, saved_row={"ppct": "13"})
        self.assertEqual(cache.get(SLOTS[0]), {"ppct": "13"})


class ScheduleJobTests(unittest.TestCase):
    def test_all_slots_saved_with_increasing_ppct(self):
        app, events, done, statuses = run_job()
        self.assertEqual(statuses, ["success"] * 6)
        fills = [c for c in FakeBridge.last.calls if c[0] == "fill"]
        self.assertEqual(fills, [("fill", 1, "10"), ("fill", 1, "11"), ("fill", 1, "12"),
                                 ("fill", 2, "13"), ("fill", 2, "14"), ("fill", 2, "15")])
        self.assertFalse(done["stopped"])
        self.assertEqual((done["completed"], done["errors"], done["next_ppct"]), (6, 0, 16))

    def test_existing_row_is_skipped_and_ppct_synced(self):
        rows = [table_row(0, SLOTS[0], has_data=True, ppct="Tiết 20")] + [table_row(i, s) for i, s in enumerate(SLOTS) if i]
        app, events, done, statuses = run_job({"rows_by_week": {1: rows}}, {"tuan_to": 1})
        self.assertEqual(statuses, ["skipped_existing", "success", "success"])
        fills = [c[2] for c in FakeBridge.last.calls if c[0] == "fill"]
        self.assertEqual(fills, ["21", "22"])
        self.assertIn(("ppct_sync", 20, 21), events)

    def test_first_add_button_index_zero_is_clicked(self):
        run_job(params={"tuan_to": 1})
        clicks = [c for c in FakeBridge.last.calls if c[0] == "click"]
        self.assertEqual(clicks[0], ("click", 1, 0, 1))

    def test_click_failure_moves_on_but_form_not_open_keeps_checkpoint(self):
        app, events, done, statuses = run_job(
            {"click_seq": [(False, "click fail")], "form_seq": [(False, "not open")]}, {"tuan_to": 1})
        self.assertEqual(statuses, ["error_click_add", "error_open_form", "success"])
        # Bấm "+" lỗi vẫn tiến checkpoint; form không mở thì không (giữ hành vi cũ).
        checkpoints = [(e[1]["next_tuan_num"], e[1]["next_slot_idx"]) for e in events if e[0] == "checkpoint"]
        self.assertEqual(checkpoints, [(1, 0), (1, 1), (1, 1), (1, 2), (2, 0)])

    def test_ambiguous_save_stops_whole_run(self):
        """Lưu mơ hồ ở Tuần 1 -> dừng hẳn, không lưu gì ở Tuần 2, checkpoint giữ slot đang dở."""
        script = {"save_seq": [(True, "saved", {}), (False, "save lỗi", {"request_sent": True})]}
        app, events, done, statuses = run_job(script)
        self.assertEqual(statuses, ["success", "error_save_ambiguous"])
        self.assertNotIn(("select", "tuan", "Tuần 2"), FakeBridge.last.calls)
        self.assertEqual([c for c in FakeBridge.last.calls if c[0] == "save"], [("save", 1), ("save", 1)])
        self.assertTrue(done["stopped"])
        self.assertEqual((done["resume_state"]["next_tuan_num"], done["resume_state"]["next_slot_idx"]), (1, 1))
        self.assertEqual(done["resume_state"]["next_ppct"], 11)
        self.assertEqual(app._schedule_stop_reason, "save mơ hồ cần xác minh")

    def test_existing_row_without_ppct_stops_whole_run(self):
        rows = [table_row(i, s) for i, s in enumerate(SLOTS)]
        rows[1] = table_row(1, SLOTS[1], has_data=True, ppct="")
        app, events, done, statuses = run_job({"rows_by_week": {1: rows}})
        self.assertEqual(statuses, ["success", "error_existing_ppct"])
        self.assertNotIn(("select", "tuan", "Tuần 2"), FakeBridge.last.calls)
        self.assertEqual((done["resume_state"]["next_tuan_num"], done["resume_state"]["next_slot_idx"]), (1, 1))

    def test_user_stop_mid_week_keeps_remaining_slots(self):
        # is_set(): 1 lần đầu tuần + 1 lần mỗi slot -> dừng ngay trước slot thứ 2 của Tuần 1.
        app, events, done, statuses = run_job(stop_after=2)
        self.assertEqual(statuses, ["success"])
        self.assertTrue(done["stopped"])
        self.assertEqual((done["resume_state"]["next_tuan_num"], done["resume_state"]["next_slot_idx"]), (1, 1))

    def test_resume_starts_from_checkpoint(self):
        resume = {"next_tuan_num": 2, "next_slot_idx": 2, "next_ppct": 30, "completed": 5}
        app, events, done, statuses = run_job(params={"resume_state": resume})
        self.assertEqual(statuses, ["success"])
        self.assertEqual([c for c in FakeBridge.last.calls if c[0] == "fill"], [("fill", 2, "30")])
        self.assertEqual(done["completed"], 6)

    def test_select_week_failure_skips_week(self):
        app, events, done, statuses = run_job({"select_seq": [(False, "timeout")] * 3})
        self.assertEqual(statuses, ["error_select_tuan"] + ["success"] * 3)
        self.assertFalse(done["stopped"])

    def test_cdp_closed_stops_with_resume(self):
        app, events, done, statuses = run_job({"select_seq": [(True, "ok"), (False, CLOSED)]})
        self.assertEqual(statuses, [])
        self.assertTrue(done["stopped"])
        self.assertEqual(done["resume_state"]["next_tuan_num"], 1)

    def test_save_error_but_table_updated_counts_as_success(self):
        script = {"save_seq": [(False, "save lỗi", {})], "fetch_seq": [(True, "đã có", None)]}
        app, events, done, statuses = run_job(script, {"tuan_to": 1})
        self.assertEqual(statuses, ["success"] * 3)
        self.assertIn(("close",), FakeBridge.last.calls)


if __name__ == "__main__":
    unittest.main()
