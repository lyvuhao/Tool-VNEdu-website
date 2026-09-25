"""VnEduScoreEntryAutomation: automation Sổ điểm cho luồng nhập điểm."""

from __future__ import annotations

import time

from ..config import ProgressCallback
from ..scorebook_core import ScorebookContext, ScoreOption, VnEduScoreAutomation
from .access_scan import (
    _patched_load_accessible_scorebook_context,
    _patched_select_accessible_scorebook_context,
    _patched_select_scorebook_context_on_page,
    _patched_wait_for_scorebook_permission_snapshot,
)
from .permissions import (
    _patched_apply_access_entries_to_context,
    _patched_can_comment_scorebook,
    _patched_find_username_input,
    _patched_find_visible_input,
    _patched_login_if_needed_on_page,
    _patched_read_login_surface_state,
    _patched_subject_options_by_class_for_current_grade,
)


# NOTE: Monkey-patching for load_accessible_scorebook_context
#       and select_accessible_scorebook_context removed — now in subclass.


class VnEduScoreEntryAutomation(VnEduScoreAutomation):
    """Subclass chuyên biệt cho chức năng nhập điểm bằng giọng nói.

    Thay thế monkey-patching pattern cũ bằng proper method overrides.
    Tất cả _patched_* functions được ủy thác (delegate) từ đây.
    """

    def _load_live_combo_options(self, page: object, combo_id: str) -> list[dict[str, object]]:
        """PERF (accuracy-safe): thoát nhanh chu trình expand ExtJS khi store đã ổn định.

        Bản gốc luôn expand → poll 180ms → chờ stable → collapse (deadline 2.5s) cho
        MỌI combo, kể cả khi store đã nạp đầy. Trong luồng dò quyền, hàm này được gọi
        ~8 lần (grade/class/subject/term × nhiều lần hydrate) nên là bottleneck chính.

        Tối ưu giữ NGUYÊN ngữ nghĩa "stability detection" (đảm bảo 100% chính xác,
        không đọc danh sách cũ sau khi đổi combo cha):
          1. Đọc store trực tiếp. Nếu có >=2 option, đọc xác nhận lần 2 sau một khoảng
             ngắn. Hai lần đọc GIỐNG HỆT nhau => store đã ổn định (không ở giữa quá
             trình reload bất đồng bộ) => trả về ngay, KHÔNG expand.
          2. Nếu chưa ổn định / lazy (<=1 option hoặc 2 lần đọc khác nhau) => expand
             rồi poll như cũ nhưng với ngân sách siết chặt hơn (90ms / 1.5s).

        Cơ chế "2 lần đọc giống nhau mới tin" giống hệt yêu cầu stable của bản gốc,
        nên không nới lỏng độ chính xác; chỉ cắt thời gian chờ ở nhánh đã ổn định.
        """
        normalized_combo_id = str(combo_id or "").strip()
        if not normalized_combo_id:
            return []

        def _signature(items: list[dict[str, object]]) -> tuple[tuple[str, str], ...]:
            return tuple(
                (str(item.get("id", "")).strip(), str(item.get("ten", "")).strip())
                for item in items
            )

        # FAST PATH: store đã ổn định và đầy => xác nhận bằng 2 lần đọc rồi trả ngay.
        first_items = self._read_combo_store_items(page, normalized_combo_id)
        if len(first_items) >= 2:
            try:
                page.wait_for_timeout(70)
            except Exception:
                return first_items
            confirm_items = self._read_combo_store_items(page, normalized_combo_id)
            if confirm_items and _signature(confirm_items) == _signature(first_items):
                return confirm_items
            # 2 lần đọc khác nhau => store có thể đang reload => đi xuống nhánh expand.
            first_items = confirm_items if len(confirm_items) > len(first_items) else first_items

        # SLOW PATH: expand để materialize store lazy, rồi poll chờ ổn định.
        try:
            page.evaluate(
                """(comboId) => {
                if (typeof Ext === 'undefined') return false;
                const cmp = Ext.getCmp(comboId);
                if (!cmp) return false;
                try {
                    if (cmp.onTriggerClick) { cmp.onTriggerClick(); }
                    else if (cmp.expand) { cmp.expand(); }
                    return true;
                } catch (error) { return false; }
            }""",
                normalized_combo_id,
            )
        except Exception:
            return first_items

        best_items = first_items
        last_signature: tuple[tuple[str, str], ...] = tuple()
        deadline = time.time() + 1.5
        while time.time() < deadline:
            try:
                page.wait_for_timeout(90)
            except Exception:
                break
            current_items = self._read_combo_store_items(page, normalized_combo_id)
            if len(current_items) > len(best_items):
                best_items = current_items
            current_signature = _signature(current_items)
            if current_signature and current_signature == last_signature:
                if current_items:
                    best_items = current_items
                break
            last_signature = current_signature

        try:
            page.evaluate(
                """(comboId) => {
                if (typeof Ext === 'undefined') return false;
                const cmp = Ext.getCmp(comboId);
                if (!cmp) return false;
                try { if (cmp.collapse) cmp.collapse(); return true; }
                catch (error) { return false; }
            }""",
                normalized_combo_id,
            )
        except Exception:
            pass

        return best_items

    def _can_comment_scorebook(
        self, permission_text: str, enabled_comment_input_count: int
    ) -> bool:
        return _patched_can_comment_scorebook(
            self, permission_text, enabled_comment_input_count
        )

    def _apply_access_entries_to_context(
        self,
        context: ScorebookContext,
        entries: list[object],
        grade_id: str,
        term_id: str,
    ) -> ScorebookContext:
        return _patched_apply_access_entries_to_context(
            self, context, entries, grade_id, term_id
        )

    def _subject_options_by_class_for_current_grade(
        self,
        page: object,
        snapshot: dict[str, object],
        grade_id: str,
        term_id: str,
        class_options: list[ScoreOption],
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        return _patched_subject_options_by_class_for_current_grade(
            self, page, snapshot, grade_id, term_id, class_options
        )

    def _find_visible_input(self, page: object, selectors: list[str]):
        return _patched_find_visible_input(self, page, selectors)

    def _find_username_input(self, page: object):
        return _patched_find_username_input(self, page)

    def _read_login_surface_state(
        self, page: object
    ) -> dict[str, object]:
        return _patched_read_login_surface_state(self, page)

    def _login_if_needed_on_page(
        self,
        page: object,
        username: str = "",
        password: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> str:
        return _patched_login_if_needed_on_page(
            self, page, username, password, progress_callback
        )

    def _wait_for_scorebook_permission_snapshot(
        self,
        page: object,
        expected_class_id: str | None = None,
        expected_subject_id: str | None = None,
        expected_term_id: str | None = None,
        timeout_sec: float = 6.0,
        progress_callback: ProgressCallback | None = None,
        progress_message: str = "Đang chờ Sổ điểm cập nhật quyền và giáo viên...",
    ) -> dict[str, object]:
        return _patched_wait_for_scorebook_permission_snapshot(
            self,
            page,
            expected_class_id,
            expected_subject_id,
            expected_term_id,
            timeout_sec,
            progress_callback,
            progress_message,
        )

    def _select_scorebook_context_on_page(
        self,
        page: object,
        snapshot: dict[str, object],
        grade_id: str = "",
        class_id: str = "",
        subject_id: str = "",
        term_id: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[dict[str, object], str]:
        return _patched_select_scorebook_context_on_page(
            self, page, snapshot, grade_id, class_id, subject_id,
            term_id, progress_callback,
        )

    def load_accessible_scorebook_context(
        self,
        username: str = "",
        password: str = "",
    ) -> tuple[object, str, str]:
        return _patched_load_accessible_scorebook_context(
            self, username, password
        )

    def select_accessible_scorebook_context(
        self,
        grade_id: str = "",
        class_id: str = "",
        subject_id: str = "",
        term_id: str = "",
        username: str = "",
        password: str = "",
    ) -> tuple[object, str, str]:
        return _patched_select_accessible_scorebook_context(
            self, grade_id, class_id, subject_id, term_id,
            username, password,
        )
