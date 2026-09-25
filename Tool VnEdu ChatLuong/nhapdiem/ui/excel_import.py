"""Nhập điểm từ file Excel/CSV: chọn file, xem trước kết quả khớp, đưa vào hàng "Chờ ghi"."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..excel_import import (
    CONFIDENT_MATCHES,
    ColumnChoice,
    ImportedTable,
    ImportFileError,
    ImportPlanRow,
    build_import_plan,
    guess_columns,
    read_score_table,
    summarize_plan,
)
from ..models import RowStatus

_NOT_USED = "(không dùng)"
_CHECKED = "☑"
_UNCHECKED = "☐"


class ExcelImportMixin:
    """Nhập điểm từ file Excel/CSV vào hàng "Chờ ghi" (ghi lên web vẫn bằng nút GHI ĐIỂM LÊN WEB)."""

    def on_import_scores_from_file(self) -> None:
        if self._busy:
            return
        if not self._stop_tree_editor(commit=True):
            return
        if not self._score_rows_by_key:
            messagebox.showinfo(
                "Chưa có danh sách học sinh",
                "Hãy chọn lớp, môn, cột điểm đích và quét danh sách học sinh trước khi nhập điểm từ file.",
            )
            return
        initial_dir = getattr(self, "_score_import_last_dir", "") or str(Path.home())
        file_path = filedialog.askopenfilename(
            parent=self.root,
            title="Chọn file điểm (Excel hoặc CSV)",
            initialdir=initial_dir,
            filetypes=[("Excel / CSV", "*.xlsx *.xlsm *.csv"), ("Excel", "*.xlsx *.xlsm"), ("CSV", "*.csv"), ("Tất cả", "*.*")],
        )
        if not file_path:
            return
        self._score_import_last_dir = str(Path(file_path).parent)
        try:
            table = read_score_table(Path(file_path))
        except ImportFileError as error:
            messagebox.showerror("Không đọc được file", str(error), parent=self.root)
            self._log(f"Nhập điểm từ file thất bại: {error}")
            return
        self._log(f"Đã đọc {len(table.rows)} dòng từ {Path(file_path).name} (sheet: {table.sheet_name}).")
        self._open_score_import_dialog(table)

    # ------------------------------------------------------------------

    def _open_score_import_dialog(self, table: ImportedTable) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Nhập điểm từ file — {table.source_path.name}")
        dialog.transient(self.root)
        dialog.geometry("1100x620")
        dialog.minsize(820, 420)

        state: dict[str, object] = {"table": table, "plan": []}
        target_column = self.target_score_column_var.get().strip() or "(chưa chọn)"
        ttk.Label(
            dialog,
            text=(
                f"Cột điểm đích trên VNEDU: {target_column}.  Điểm được đưa vào cột \"Điểm chờ\" — "
                "kiểm tra lại rồi bấm GHI ĐIỂM LÊN WEB như bình thường. Bấm vào ô ☑/☐ để chọn/bỏ từng dòng."
            ),
            wraplength=1060,
            justify=tk.LEFT,
        ).pack(fill=tk.X, padx=10, pady=(10, 4))

        selectors = ttk.Frame(dialog)
        selectors.pack(fill=tk.X, padx=10, pady=4)
        sheet_var = tk.StringVar(value=table.sheet_name)
        column_vars = {key: tk.StringVar() for key in ("name", "given", "code", "score")}
        # Hàng 1: sheet + cột điểm (quan trọng nhất); hàng 2: các cột dùng để khớp học sinh.
        layout = (
            (0, 0, "sheet", "Sheet:"),
            (0, 1, "score", "Cột điểm cần nhập:"),
            (1, 0, "name", "Cột họ tên:"),
            (1, 1, "given", "Cột tên (nếu họ/tên tách):"),
            (1, 2, "code", "Cột mã HS:"),
        )
        combos: dict[str, ttk.Combobox] = {}
        for grid_row, slot, key, text in layout:
            ttk.Label(selectors, text=text).grid(row=grid_row, column=slot * 2, padx=(0, 4), pady=2, sticky="w")
            variable = sheet_var if key == "sheet" else column_vars[key]
            combo = ttk.Combobox(selectors, textvariable=variable, state="readonly", width=22)
            combo.grid(row=grid_row, column=slot * 2 + 1, padx=(0, 16), pady=2, sticky="w")
            combos[key] = combo
        combos["sheet"]["values"] = table.sheet_names

        tree_frame = ttk.Frame(dialog)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        columns = ("pick", "row", "source", "score", "student", "current", "result")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        for key, heading, width, anchor in (
            ("pick", "Ghi", 56, "center"),
            ("row", "Dòng", 64, "center"),
            ("source", "Họ tên trong file", 200, "w"),
            ("score", "Điểm", 64, "center"),
            ("student", "Học sinh trên VNEDU", 200, "w"),
            ("current", "Điểm hiện tại", 130, "center"),
            ("result", "Kết quả", 360, "w"),
        ):
            tree.heading(key, text=heading)
            tree.column(key, width=width, anchor=anchor, stretch=key in ("source", "student", "result"))
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.tag_configure("ok", background="#ecfdf3")
        tree.tag_configure("review", background="#fef9c3")
        tree.tag_configure("skip", foreground="#64748b")
        tree.tag_configure("error", background="#fee2e2")

        summary_var = tk.StringVar()
        ttk.Label(dialog, textvariable=summary_var, wraplength=1060, justify=tk.LEFT).pack(fill=tk.X, padx=10, pady=(2, 4))

        buttons = ttk.Frame(dialog)
        buttons.pack(fill=tk.X, padx=10, pady=(0, 10))
        apply_button = ttk.Button(buttons, text="Đưa vào hàng chờ")
        ttk.Button(buttons, text="Huỷ", command=dialog.destroy).pack(side=tk.RIGHT)
        apply_button.pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(buttons, text="Bỏ chọn tất cả", command=lambda: set_all(False)).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Chọn tất cả dòng khớp", command=lambda: set_all(True)).pack(side=tk.LEFT, padx=(6, 0))

        def column_index(key: str) -> int | None:
            value = column_vars[key].get()
            headers = state["table"].headers  # type: ignore[union-attr]
            return headers.index(value) if value in headers else None

        def set_column_choices(choice: ColumnChoice) -> None:
            headers = list(state["table"].headers)  # type: ignore[union-attr]
            values = [_NOT_USED, *headers]
            for key in column_vars:
                combos[key]["values"] = values
                index = getattr(choice, key)
                column_vars[key].set(headers[index] if index is not None and index < len(headers) else _NOT_USED)

        def render() -> None:
            tree.delete(*tree.get_children())
            plan: list[ImportPlanRow] = state["plan"]  # type: ignore[assignment]
            for index, item in enumerate(plan):
                if not item.row_key or item.status in ("invalid_score",):
                    tag = "error"
                elif not item.can_apply:
                    tag = "skip"
                elif item.status in CONFIDENT_MATCHES:
                    tag = "ok"
                else:
                    tag = "review"
                pick = (_CHECKED if item.selected else _UNCHECKED) if item.can_apply else ""
                tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        pick,
                        item.source_row,
                        item.source_name or item.source_code,
                        item.score or item.raw_score,
                        item.student_name,
                        item.pending_score and f"{item.current_score} (chờ {item.pending_score})" or item.current_score,
                        item.detail,
                    ),
                    tags=(tag,),
                )
            selected_count = sum(1 for item in plan if item.selected)
            summary_var.set(summarize_plan(plan, len(self._score_rows_by_key)) + f"\nĐang chọn {selected_count} dòng để đưa vào hàng chờ.")
            apply_button.configure(text=f"Đưa {selected_count} điểm vào hàng chờ", state=("normal" if selected_count else "disabled"))

        def rebuild(_event=None) -> None:
            choice = ColumnChoice(
                name=column_index("name"),
                given=column_index("given"),
                code=column_index("code"),
                score=column_index("score"),
            )
            with self._score_data_lock:
                students = list(self._score_rows_by_key.values())
            try:
                state["plan"] = build_import_plan(state["table"], choice, students)  # type: ignore[arg-type]
            except ImportFileError as error:
                state["plan"] = []
                render()
                summary_var.set(str(error))
                return
            render()

        def change_sheet(_event=None) -> None:
            try:
                new_table = read_score_table(table.source_path, sheet_var.get())
            except ImportFileError as error:
                messagebox.showerror("Không đọc được sheet", str(error), parent=dialog)
                return
            state["table"] = new_table
            set_column_choices(guess_columns(new_table))
            rebuild()

        def toggle(event) -> None:
            if tree.identify_region(event.x, event.y) != "cell" or tree.identify_column(event.x) != "#1":
                return
            iid = tree.identify_row(event.y)
            plan: list[ImportPlanRow] = state["plan"]  # type: ignore[assignment]
            if not iid or not plan[int(iid)].can_apply:
                return
            plan[int(iid)].selected = not plan[int(iid)].selected
            render()
            tree.see(iid)

        def set_all(selected: bool) -> None:
            for item in state["plan"]:  # type: ignore[union-attr]
                item.selected = selected and item.can_apply
            render()

        def apply() -> None:
            chosen = [item for item in state["plan"] if item.selected and item.can_apply]  # type: ignore[union-attr]
            if not chosen:
                return
            review = [item for item in chosen if item.status not in CONFIDENT_MATCHES]
            if review and not messagebox.askyesno(
                "Xác nhận",
                f"Có {len(review)} dòng khớp chưa chắc chắn (trùng tên / gần đúng). Vẫn đưa vào hàng chờ?",
                parent=dialog,
            ):
                return
            self._apply_imported_scores(chosen, state["table"].source_path.name)  # type: ignore[union-attr]
            dialog.destroy()

        apply_button.configure(command=apply)
        tree.bind("<Button-1>", toggle, add="+")
        combos["sheet"].bind("<<ComboboxSelected>>", change_sheet)
        for key in column_vars:
            combos[key].bind("<<ComboboxSelected>>", rebuild)
        set_column_choices(guess_columns(table))
        rebuild()
        dialog.grab_set()
        dialog.focus_set()
        self._score_import_dialog = dialog

    def _apply_imported_scores(self, items: list[ImportPlanRow], source_name: str) -> None:
        applied = 0
        for item in items:
            row = self._score_rows_by_key.get(item.row_key)
            if row is None:
                continue
            self._apply_row_patch(
                item.row_key,
                pending_score=item.score,
                status=RowStatus.PENDING,
                recognized_text=f"File: {item.source_name or item.source_code} (dòng {item.source_row})",
                match_score=100 if item.status in CONFIDENT_MATCHES else 0,
                reason=f"nhập từ file {source_name}",
            )
            applied += 1
        self._log(f"📄 Đã đưa {applied} điểm từ {source_name} vào hàng chờ. Kiểm tra rồi bấm GHI ĐIỂM LÊN WEB.")
        messagebox.showinfo(
            "Đã nhập điểm",
            f"Đã đưa {applied} điểm vào cột \"Điểm chờ\".\n\n"
            "Hãy kiểm tra lại bảng, sau đó bấm GHI ĐIỂM LÊN WEB để ghi lên VNEDU.\n"
            "Có thể hoàn tác từng dòng bằng Ctrl+Z.",
            parent=self.root,
        )
