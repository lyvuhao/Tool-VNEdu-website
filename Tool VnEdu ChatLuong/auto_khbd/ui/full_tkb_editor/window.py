"""FullTKBEditorWindow: phóng to lưới TKB, xem lẻ-chẵn cạnh nhau."""

from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

from ..theme import CLR_PANEL_BG
from .clipboard import ClipboardMixin
from .layout import EditorLayoutMixin
from .lifecycle import EditorLifecycleMixin
from .mouse import MouseEventsMixin
from .render import RenderMixin

if TYPE_CHECKING:  # chỉ dùng cho chú thích kiểu
    from ..wizard.wizard import KHDHWizard


class FullTKBEditorWindow(
    EditorLayoutMixin,
    RenderMixin,
    MouseEventsMixin,
    ClipboardMixin,
    EditorLifecycleMixin,
    tk.Toplevel,
):
    """Cửa sổ phóng to TKB cho dễ quan sát + chỉnh sửa.

    2 chế độ hiển thị:
        VIEW_SINGLE  — 1 grid duy nhất (template active của wizard).
                       Mặc định khi tach_le_chan=OFF, hoặc user chọn xem
                       1 mẫu.
        VIEW_LE_CHAN — 2 grid cạnh nhau (lẻ trái, chẵn phải).
                       Chỉ enable khi tach_le_chan=ON.

    Tương tác đầy đủ giống grid wizard chính:
        - **Single-click**  → select ô (highlight viền vàng).
        - **Double-click**  → mở `SlotPickerDialog` để edit.
        - **Drag**          → copy ô có data sang ô khác (cross-panel OK
                              khi MODE_LE_CHAN — kéo từ TKB lẻ sang
                              TKB chẵn ghi vào template chẵn).
        - **Right-click**   → menu Sửa / Sao chép / Dán / Xóa.
        - **Ctrl+C / Ctrl+V** → copy/paste qua `_slot_clipboard` (share
                              với wizard chính).
        - **Delete**        → xóa ô đang select.

    Auto-fit:
        Mặc định mở ở trạng thái MAXIMIZE (state='zoomed' trên Windows).
        Sizes cell tính động dựa trên screen size để 1 hoặc 2 grid hiển
        thị TRỌN VẸN không cần scroll khi screen ≥ 1366×768. Khi user
        thu nhỏ window, scrollbar tự xuất hiện nếu cần.
    """

    MODE_SINGLE = "single"

    MODE_LE_CHAN = "le_chan"

    # Min sizes — không cho cell nhỏ hơn để text vẫn đọc được khi window
    # bị thu nhỏ quá mức. Nếu vẫn không đủ → scrollbar bật.
    MIN_COL_DAY_W = 70

    MIN_ROW_BODY_H = 44

    # Max sizes — KHÔNG cap COL_DAY_W nữa (set rất cao) để single mode
    # fill hết chiều ngang screen, tránh khoảng trống bên phải.
    MAX_COL_DAY_W = 999

    MAX_ROW_BODY_H = 78

    # Sizes cố định cho overhead UI (header, separator, buoi, tiet col).
    ROW_HEADER_H = 32

    SEPARATOR_H = 16

    COL_BUOI_W = 60

    COL_TIET_W = 38

    # Header window + body padding overhead (toolbar + status + window chrome).
    WIN_VERTICAL_OVERHEAD = 130

    WIN_HORIZONTAL_OVERHEAD = 40

    def __init__(self, parent: "KHDHWizard"):
        super().__init__(parent)
        self.wizard = parent
        self.title("🔍 Phóng to TKB")
        self.configure(background=CLR_PANEL_BG)
        self.transient(parent.winfo_toplevel())
        self.resizable(True, True)

        # Auto-fit + maximize: đặt geometry hợp lý trước khi zoomed để khi
        # user un-maximize, window vẫn có size hợp lệ.
        scr_w = self.winfo_screenwidth()
        scr_h = self.winfo_screenheight()
        unzoomed_w = int(scr_w * 0.92)
        unzoomed_h = int(scr_h * 0.90)
        x = max(0, (scr_w - unzoomed_w) // 2)
        y = max(0, (scr_h - unzoomed_h) // 2)
        self.geometry(f"{unzoomed_w}x{unzoomed_h}+{x}+{y}")
        self.minsize(min(960, scr_w - 40), min(620, scr_h - 80))
        # Maximize ngay khi mở — Tk Windows dùng state='zoomed'.
        # Wrap try/except: macOS/Linux không hỗ trợ 'zoomed' → fallback
        # về geometry size lớn nhưng không maximize.
        try:
            self.state("zoomed")
        except Exception:
            pass

        # State
        self.var_mode = tk.StringVar(
            value=(
                self.MODE_LE_CHAN
                if (parent.profile and parent.profile.tach_le_chan)
                else self.MODE_SINGLE
            )
        )
        self.var_single_target = tk.StringVar(value="le")

        # Each panel: dict with keys
        #   canvas: tk.Canvas
        #   inner:  tk.Frame
        #   buttons: dict[row_key, tk.Button]
        #   tag:    "chinh" | "le" | "chan"
        #   sizes:  (COL_DAY_W, ROW_BODY_H)
        self._panels: list[dict] = []
        # O(1) widget→(panel_idx, key) lookup cache — populated by
        # _build_grid_in, cleared by _render_body. Dùng id(widget) làm key
        # vì widget identity check nhanh hơn so với linear scan.
        self._widget_to_cell: dict[int, tuple[int, str]] = {}

        # Selection state — nhớ panel nào đang chứa ô selected để cross-panel
        # KB shortcuts biết target. (-1 = không có).
        self._selected_panel_idx: int = -1
        self._selected_key: str | None = None
        # Drag state — lưu tham chiếu source panel + key. Cross-panel OK.
        self._drag_state: dict | None = None
        # Single context menu instance — tái dùng để giảm leak.
        self._context_menu: tk.Menu | None = None
        self._context_menu_target: tuple[int, str] | None = None
        # ↑ (panel_idx, key) — set khi popup mở để các handler menu biết
        # đúng ô. Reset về None khi menu unposted.

        # Status bar message — feedback cho user khi copy/paste/delete.
        self.var_status = tk.StringVar(value="")

        # Track keyboard binding funcids ở Toplevel để unbind clean khi
        # destroy. Cùng pattern với wizard chính (_kb_bound_funcids).
        self._kb_bound_funcids: list[tuple[str, str]] = []

        self._build_ui()
        self._bind_keyboard_shortcuts()

        # Window-level binds
        self.bind("<Escape>", self._on_escape)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        # KHÔNG bind <Configure> để rebuild grid — đây là root cause gây
        # flicker loop (rebuild → geometry change → trigger Configure lại).
        # Sizes được compute 1 lần khi _render_body() chạy (init + mode
        # change). Khi user un-maximize/resize → scrollbar tự bật nếu grid
        # lớn hơn viewport. User muốn re-fit → switch mode hoặc đóng/mở lại.
        # Cleanup khi destroy
        self.bind("<Destroy>", self._on_destroy, add="+")

        self._refresh_all_panels()
