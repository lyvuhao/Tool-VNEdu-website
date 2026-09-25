"""Tab Bản lưu nhanh tự động."""

from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ...engine.backup.snapshots import SnapshotManager


class SnapshotsTabMixin:
    """Tab Bản lưu nhanh tự động."""

    # -----------------------------------------------------------
    # Tab 3: Bản lưu nhanh tự động
    # -----------------------------------------------------------

    def _build_snapshots_tab(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        ttk.Label(
            parent,
            text=(
                "Công cụ tự tạo bản sao lưu nhanh mỗi lần bạn bấm 'Bắt đầu nhập' "
                "hoặc 'Khôi phục'. Bản lưu nhanh được lưu cạnh tệp Excel hồ sơ "
                "(thư mục `<tên-tệp>.snapshots/`). Giữ tối đa 10 tệp mới "
                "nhất, tự xóa tệp > 30 ngày.\n"
                "Nháy đúp 1 bản sao lưu nhanh để mở trong tab Khôi phục."
            ),
            wraplength=920, justify="left",
            style="WizHint.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        bar = ttk.Frame(parent, style="Wiz.TFrame")
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(
            bar, text="🔄 Tải lại danh sách",
            command=self._refresh_snapshots_list,
            style="WizSubtle.TButton",
        ).pack(side="left")
        ttk.Button(
            bar, text="📂 Mở thư mục…",
            command=self._open_snapshots_folder,
            style="WizSubtle.TButton",
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            bar, text="🗑 Xóa bản đã chọn",
            command=self._delete_selected_snapshot,
            style="WizDanger.TButton",
        ).pack(side="right")

        wrap = ttk.Frame(parent, style="Wiz.TFrame")
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        cols = ("created", "reason", "size", "note", "path")
        self.tree_snap = ttk.Treeview(
            wrap, columns=cols, show="headings",
            style="Wiz.Treeview",
        )
        for col, label, w, anchor, stretch in [
            ("created", "Thời gian tạo", 150, "center", False),
            ("reason", "Lý do", 130, "center", False),
            ("size", "Kích thước", 90, "center", False),
            ("note", "Ghi chú", 250, "w", False),
            ("path", "Tệp", 380, "w", True),
        ]:
            self.tree_snap.heading(col, text=label)
            self.tree_snap.column(col, width=w, anchor=anchor, stretch=stretch)
        self.tree_snap.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(
            wrap, orient="vertical", command=self.tree_snap.yview,
        )
        sb.grid(row=0, column=1, sticky="ns")
        self.tree_snap.configure(yscrollcommand=sb.set)
        self.tree_snap.bind("<Double-Button-1>", self._on_snapshot_dbl_click)

        self.var_snap_status = tk.StringVar(value="")
        ttk.Label(
            parent, textvariable=self.var_snap_status,
            style="WizHint.TLabel",
        ).grid(row=3, column=0, sticky="w", pady=(4, 0))

    def _refresh_snapshots_list(self):
        if not hasattr(self, "tree_snap"):
            return
        for iid in self.tree_snap.get_children():
            self.tree_snap.delete(iid)
        # Dùng thư mục chung khi chưa có hồ sơ Excel.
        ctx = getattr(self.wizard, "ctx_info", None)
        gv_id = int(getattr(ctx, "giao_vien_id", 0) or 0) if ctx else 0
        nam_hoc = int(getattr(ctx, "nam_hoc", 0) or 0) if ctx else 0
        if self.wizard._profile_path is None and gv_id <= 0:
            self.var_snap_status.set(
                "(Chưa có hồ sơ Excel hoặc chưa đăng nhập VnEdu — "
                "không xác định được thư mục bản sao lưu nhanh)"
            )
            self._snapshot_list = []
            return
        mgr = SnapshotManager(
            self.wizard._profile_path,
            gv_id=gv_id, nam_hoc=nam_hoc,
        )
        snaps = mgr.list_snapshots()
        self._snapshot_list = snaps
        if not snaps:
            self.var_snap_status.set(
                "(Thư mục trống — chưa có bản sao lưu nhanh nào)"
            )
            return
        for s in snaps:
            note = mgr.load_note(s)
            size_kb = s.size_bytes / 1024
            size_str = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
            self.tree_snap.insert(
                "", "end", iid=str(s.path),
                values=(
                    s.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    s.reason,
                    size_str,
                    note,
                    str(s.path),
                ),
            )
        total_size = sum(s.size_bytes for s in snaps) / 1024
        self.var_snap_status.set(
            f"{len(snaps)} bản sao lưu nhanh • Tổng dung lượng: {total_size:.0f} KB"
        )

    def _open_snapshots_folder(self):
        ctx = getattr(self.wizard, "ctx_info", None)
        gv_id = int(getattr(ctx, "giao_vien_id", 0) or 0) if ctx else 0
        nam_hoc = int(getattr(ctx, "nam_hoc", 0) or 0) if ctx else 0
        if self.wizard._profile_path is None and gv_id <= 0:
            messagebox.showinfo(
                "Chưa có hồ sơ",
                "Hãy mở 1 tệp hồ sơ Excel hoặc đăng nhập VnEdu trước.",
                parent=self,
            )
            return
        mgr = SnapshotManager(
            self.wizard._profile_path,
            gv_id=gv_id, nam_hoc=nam_hoc,
        )
        d = mgr.ensure_dir()
        if d is None:
            messagebox.showerror(
                "Không tạo được thư mục",
                "Không thể tạo thư mục bản lưu nhanh.",
                parent=self,
            )
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(d))
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(d)])
        except Exception as e:
            messagebox.showerror(
                "Lỗi mở thư mục",
                f"{type(e).__name__}: {e}",
                parent=self,
            )

    def _delete_selected_snapshot(self):
        sel = self.tree_snap.selection()
        if not sel:
            messagebox.showinfo(
                "Chưa chọn",
                "Hãy chọn 1 bản lưu nhanh để xóa.",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "Xóa bản lưu nhanh",
            f"Xóa {len(sel)} bản lưu nhanh? Hành động này không hoàn tác được.",
            parent=self,
        ):
            return
        deleted = 0
        for iid in sel:
            try:
                Path(iid).unlink()
                deleted += 1
            except Exception:
                pass
        self._refresh_snapshots_list()
        self.var_snap_status.set(f"✓ Đã xóa {deleted} bản lưu nhanh.")

    def _on_snapshot_dbl_click(self, _event=None):
        sel = self.tree_snap.selection()
        if not sel:
            return
        path = sel[0]
        # Switch sang tab Khôi phục + load file
        self.var_restore_path.set(path)
        try:
            self._notebook.select(1)  # Tab Khôi phục
        except Exception:
            pass
        self._on_reload_restore_file()
