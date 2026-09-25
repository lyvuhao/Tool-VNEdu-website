"""Hộp thoại thêm / thay thế tool."""

from __future__ import annotations

import shutil
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..config import (
    ACCENT_CHOICES,
    CUSTOM_TOOLS_DIR_NAME,
    EMBEDDED_STATUS_COLORS,
    PANEL_COLOR,
    SURFACE_COLOR,
    TEXT_COLOR,
    TOOL_ACCENTS,
    TOOL_STATUS_COLORS,
    UI_FONT_FAMILY,
)
from ..custom_tools import (
    custom_tool_external_path,
    custom_tool_id_for_title,
    default_tool_payload,
    load_custom_tools,
    load_default_tool_overrides,
    save_custom_tools,
    save_default_tool_overrides,
    tool_workspace_dir,
)
from ..embedded import (
    encode_embedded_tool_source,
    refresh_embedded_payloads_in_control_panel,
    sha256_hex,
)
from ..tool_registry import is_launcher_source, TOOL_FILES


class ToolDialogsMixin:
    """Hộp thoại thêm / thay thế tool."""

    def _open_add_tool_dialog(self) -> None:
        """Open a step-by-step wizard used to add a new custom Python tool card."""

        dialog = tk.Toplevel(self.root)
        dialog.title("Thêm tool")
        dialog.resizable(False, False)
        dialog.configure(background=SURFACE_COLOR)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)

        title_var = tk.StringVar()
        description_var = tk.StringVar()
        color_var = tk.StringVar(value="Xanh ngọc")
        selected_file = tk.StringVar(value="Chưa chọn file .py")
        wizard_status_var = tk.StringVar(value="Bước 1/5: chọn file Python cần thêm.")
        current_step = tk.IntVar(value=0)
        selected_path: dict[str, Path | None] = {"path": None}
        validation_state: dict[str, object] = {"ok": False, "messages": []}

        shell = ttk.Frame(dialog, style="App.TFrame", padding=(18, 16, 18, 16))
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)

        ttk.Label(
            shell,
            text="Thêm tool vào dashboard",
            style="DialogTitle.TLabel",
            font=(UI_FONT_FAMILY, 17, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            shell,
            text="Chọn file, nhập thông tin, kiểm tra rồi lưu. Tool sẽ có bản nhúng dự phòng để dùng khi mất file ngoài.",
            style="Muted.TLabel",
            wraplength=560,
        ).grid(row=1, column=0, sticky="w", pady=(5, 14))

        panel, frame = self._outlined_panel(shell, padding=(18, 14, 18, 14))
        panel.grid(row=2, column=0, sticky="ew")
        frame.columnconfigure(1, weight=1)

        def choose_file() -> None:
            path = filedialog.askopenfilename(
                parent=dialog,
                title="Chọn file Python cho tool mới",
                filetypes=[("Python file", "*.py"), ("Tất cả file", "*.*")],
            )
            if path:
                selected_path["path"] = Path(path)
                selected_file.set(path)
                if not title_var.get().strip():
                    title_var.set(Path(path).stem.replace("_", " ").title())
                validation_state["ok"] = False
                validation_state["messages"] = []
                render_step()

        def validate_current_tool() -> bool:
            messages: list[str] = []
            path = selected_path["path"]
            if path is None:
                messages.append("Chưa chọn file .py.")
            elif not path.exists() or not path.is_file():
                messages.append(f"Không tìm thấy file: {path}")
            elif path.suffix.lower() != ".py":
                messages.append("File được chọn không phải .py.")
            if not title_var.get().strip():
                messages.append("Tên card chưa được nhập.")
            try:
                if path is not None and path.exists() and path.is_file() and path.suffix.lower() == ".py":
                    source_bytes = path.read_bytes()
                    compile(source_bytes, str(path), "exec")
                    encoded = encode_embedded_tool_source(source_bytes)
                    if not encoded:
                        messages.append("Không tạo được bản nhúng dự phòng.")
            except Exception as error:  # noqa: BLE001 - user-facing wizard validation.
                messages.append(f"File Python chưa hợp lệ: {error}")
            validation_state["ok"] = not messages
            validation_state["messages"] = messages or ["Kiểm tra OK. File có thể nhúng và tạo card."]
            return bool(validation_state["ok"])

        def apply_add() -> None:
            if not bool(validation_state.get("ok")) and not validate_current_tool():
                current_step.set(3)
                render_step()
                return
            path = selected_path["path"]
            if path is None:
                current_step.set(0)
                render_step()
                return
            try:
                tool_id = self._add_custom_tool(
                    title=title_var.get(),
                    description=description_var.get(),
                    accent=ACCENT_CHOICES.get(color_var.get(), ACCENT_CHOICES["Xanh ngọc"]),
                    source_path=path,
                )
            except Exception as error:  # noqa: BLE001 - user-facing add-tool path.
                wizard_status_var.set(f"Không lưu được tool: {error}")
                validation_state["ok"] = False
                validation_state["messages"] = [str(error)]
                current_step.set(3)
                render_step()
                return
            self.custom_tools = load_custom_tools()
            self.status_var.set(f"Đã thêm tool: {self.custom_tools[tool_id]['title']}.")
            dialog.destroy()
            self._build_dashboard_screen()

        def clear_frame() -> None:
            for child in frame.winfo_children():
                child.destroy()

        def reset_validation() -> None:
            validation_state["ok"] = False
            validation_state["messages"] = []

        def render_step() -> None:
            clear_frame()
            step = current_step.get()
            step_titles = [
                "BƯỚC 1/5 - CHỌN FILE",
                "BƯỚC 2/5 - THÔNG TIN CARD",
                "BƯỚC 3/5 - MÀU CARD",
                "BƯỚC 4/5 - KIỂM TRA",
                "BƯỚC 5/5 - LƯU TOOL",
            ]
            step_messages = [
                "Chọn file .py của tool cần đưa vào dashboard.",
                "Tên card nên ngắn, mô tả nên đủ để người dùng hiểu chức năng.",
                "Chọn màu nhận diện cho card trên dashboard.",
                "App sẽ kiểm tra file và bản nhúng trước khi cho lưu.",
                "Xác nhận thông tin, sau đó lưu tool vào dashboard.",
            ]
            wizard_status_var.set(step_messages[step])
            ttk.Label(frame, text=step_titles[step], style="DialogFieldTitle.TLabel").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
            )
            if step == 0:
                ttk.Label(frame, text="FILE .PY", style="DialogFieldTitle.TLabel").grid(
                    row=1, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
                )
                ttk.Label(frame, textvariable=selected_file, style="PanelMuted.TLabel", wraplength=420).grid(
                    row=1, column=1, sticky="w", pady=(0, 10)
                )
                self._flat_button(frame, text="Chọn file .py", command=choose_file, primary=True).grid(
                    row=2, column=1, sticky="w"
                )
            elif step == 1:
                ttk.Label(frame, text="TÊN TOOL", style="DialogFieldTitle.TLabel").grid(
                    row=1, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
                )
                title_entry = ttk.Entry(frame, textvariable=title_var, width=46)
                title_entry.grid(row=1, column=1, sticky="ew", pady=(0, 10))
                ttk.Label(frame, text="MÔ TẢ", style="DialogFieldTitle.TLabel").grid(
                    row=2, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
                )
                ttk.Entry(frame, textvariable=description_var, width=46).grid(row=2, column=1, sticky="ew", pady=(0, 10))
                title_entry.focus_set()
            elif step == 2:
                ttk.Label(frame, text="MÀU CARD", style="DialogFieldTitle.TLabel").grid(
                    row=1, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
                )
                color_row = ttk.Frame(frame, style="Panel.TFrame")
                color_row.grid(row=1, column=1, sticky="ew", pady=(0, 10))
                color_combo = ttk.Combobox(
                    color_row,
                    textvariable=color_var,
                    values=list(ACCENT_CHOICES),
                    state="readonly",
                    width=18,
                )
                color_combo.grid(row=0, column=0, sticky="w")
                color_preview = tk.Frame(
                    color_row,
                    width=40,
                    height=24,
                    bg=ACCENT_CHOICES.get(color_var.get(), ACCENT_CHOICES["Xanh ngọc"]),
                )
                color_preview.grid(row=0, column=1, padx=(10, 0))
                color_preview.grid_propagate(False)

                def update_color_preview(_event: object | None = None) -> None:
                    reset_validation()
                    color_preview.configure(bg=ACCENT_CHOICES.get(color_var.get(), ACCENT_CHOICES["Xanh ngọc"]))

                color_combo.bind("<<ComboboxSelected>>", update_color_preview)
            elif step == 3:
                ok = validate_current_tool()
                color = "#047857" if ok else "#b91c1c"
                ttk.Label(
                    frame,
                    text="KẾT QUẢ",
                    style="DialogFieldTitle.TLabel",
                ).grid(row=1, column=0, sticky="nw", padx=(0, 12), pady=(0, 10))
                result_text = "\n".join(f"- {message}" for message in validation_state["messages"])
                tk.Label(
                    frame,
                    text=result_text,
                    bg=PANEL_COLOR,
                    fg=color,
                    font=(UI_FONT_FAMILY, 10, "bold"),
                    justify="left",
                    wraplength=440,
                ).grid(row=1, column=1, sticky="w", pady=(0, 10))
                self._flat_button(frame, text="Kiểm tra lại", command=render_step).grid(row=2, column=1, sticky="w")
            else:
                summary = (
                    f"Tên: {title_var.get().strip()}\n"
                    f"Mô tả: {description_var.get().strip() or 'Tool tùy chỉnh.'}\n"
                    f"Màu: {color_var.get()}\n"
                    f"File: {selected_path['path']}"
                )
                tk.Label(
                    frame,
                    text=summary,
                    bg=PANEL_COLOR,
                    fg=TEXT_COLOR,
                    font=(UI_FONT_FAMILY, 10),
                    justify="left",
                    wraplength=470,
                ).grid(row=1, column=0, columnspan=2, sticky="w")
            render_buttons()

        def can_leave_step(step: int) -> bool:
            if step == 0 and selected_path["path"] is None:
                wizard_status_var.set("Hãy chọn file .py trước.")
                return False
            if step == 1 and not title_var.get().strip():
                wizard_status_var.set("Hãy nhập tên card.")
                return False
            if step == 3 and not validate_current_tool():
                render_step()
                return False
            return True

        def go_previous() -> None:
            if current_step.get() > 0:
                current_step.set(current_step.get() - 1)
                render_step()

        def go_next() -> None:
            step = current_step.get()
            if not can_leave_step(step):
                return
            if step < 4:
                current_step.set(step + 1)
                render_step()

        button_row = ttk.Frame(shell, style="App.TFrame")
        button_row.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        button_row.columnconfigure(0, weight=1)
        status_label = ttk.Label(shell, textvariable=wizard_status_var, style="Footer.TLabel")
        status_label.grid(row=4, column=0, sticky="w", pady=(8, 0))

        def render_buttons() -> None:
            for child in button_row.winfo_children():
                child.destroy()
            if current_step.get() > 0:
                self._flat_button(button_row, text="Quay lại", command=go_previous).grid(row=0, column=1, padx=(0, 8))
            if current_step.get() < 4:
                self._flat_button(button_row, text="Tiếp", command=go_next, primary=True, width=7).grid(
                    row=0, column=2, padx=(0, 8)
                )
            else:
                self._flat_button(button_row, text="Lưu tool", command=apply_add, primary=True, width=8).grid(
                    row=0, column=2, padx=(0, 8)
                )
            self._flat_button(button_row, text="Hủy", command=dialog.destroy, width=7).grid(row=0, column=3)

        render_step()

        dialog.update_idletasks()
        x = self.root.winfo_rootx() + max((self.root.winfo_width() - dialog.winfo_width()) // 2, 0)
        y = self.root.winfo_rooty() + max((self.root.winfo_height() - dialog.winfo_height()) // 3, 0)
        dialog.geometry(f"+{x}+{y}")

    def _open_replace_tool_dialog(self, initial_tool_name: str | None = None) -> None:
        """Open a dialog for replacing tool files and editing custom tool cards."""

        self.custom_tools = load_custom_tools()
        dialog = tk.Toplevel(self.root)
        dialog.title("Đổi file tool")
        dialog.resizable(False, False)
        dialog.configure(background=SURFACE_COLOR)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        tool_labels: dict[str, tuple[str, bool]] = {}
        for tool_name, metadata in TOOL_FILES.items():
            tool_labels[f"Mặc định | {metadata['title']}  [{metadata['script']}]"] = (tool_name, False)
        for tool_id, metadata in self.custom_tools.items():
            tool_labels[f"Thêm mới | {metadata['title']}  [{Path(metadata['script']).name}]"] = (tool_id, True)

        initial_label = next(
            (label for label, (tool_name, _is_custom) in tool_labels.items() if tool_name == initial_tool_name),
            next(iter(tool_labels)),
        )
        selected_tool_label = tk.StringVar(value=initial_label)
        selected_file = tk.StringVar(value="Chưa chọn file .py")
        selected_path: dict[str, Path | None] = {"path": None}
        tool_detail_var = tk.StringVar()
        title_var = tk.StringVar()
        description_var = tk.StringVar()
        color_var = tk.StringVar(value="Xanh ngọc")

        def color_name_for_hex(color: str) -> str:
            for name, value in ACCENT_CHOICES.items():
                if value.lower() == color.lower():
                    return name
            return "Xanh ngọc"

        shell = ttk.Frame(dialog, style="App.TFrame", padding=(22, 18, 22, 18))
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)

        ttk.Label(
            shell,
            text="Đổi file tool",
            style="DialogTitle.TLabel",
            font=(UI_FONT_FAMILY, 17, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            shell,
            text="Tool mặc định chỉ đổi file. Tool thêm mới có thể đổi tên, mô tả, màu card và file nhúng.",
            style="Muted.TLabel",
            wraplength=600,
        ).grid(row=1, column=0, sticky="w", pady=(5, 14))

        panel, frame = self._outlined_panel(shell, padding=(18, 14, 18, 14))
        panel.grid(row=2, column=0, sticky="ew")
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="CHỨC NĂNG", style="DialogFieldTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
        )
        combo = ttk.Combobox(
            frame,
            textvariable=selected_tool_label,
            values=list(tool_labels),
            state="readonly",
            width=52,
        )
        combo.grid(row=0, column=1, sticky="ew", pady=(0, 10))

        ttk.Label(frame, text="TÊN HIỂN THỊ", style="DialogFieldTitle.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
        )
        title_entry = ttk.Entry(frame, textvariable=title_var, width=46)
        title_entry.grid(row=1, column=1, sticky="ew", pady=(0, 10))

        ttk.Label(frame, text="MÔ TẢ", style="DialogFieldTitle.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
        )
        description_entry = ttk.Entry(frame, textvariable=description_var, width=46)
        description_entry.grid(row=2, column=1, sticky="ew", pady=(0, 10))

        ttk.Label(frame, text="MÀU CARD", style="DialogFieldTitle.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
        )
        color_row = ttk.Frame(frame, style="Panel.TFrame")
        color_row.grid(row=3, column=1, sticky="ew", pady=(0, 10))
        color_combo = ttk.Combobox(
            color_row,
            textvariable=color_var,
            values=list(ACCENT_CHOICES),
            state="readonly",
            width=18,
        )
        color_combo.grid(row=0, column=0, sticky="w")
        color_preview = tk.Frame(color_row, width=34, height=22, bg=ACCENT_CHOICES[color_var.get()])
        color_preview.grid(row=0, column=1, padx=(10, 0))
        color_preview.grid_propagate(False)

        ttk.Label(frame, text="FILE MỚI", style="DialogFieldTitle.TLabel").grid(
            row=4, column=0, sticky="w", padx=(0, 12), pady=(2, 0)
        )
        ttk.Label(frame, textvariable=selected_file, style="PanelMuted.TLabel", wraplength=450).grid(
            row=4, column=1, sticky="w", pady=(2, 0)
        )
        ttk.Label(frame, text="ĐANG DÙNG", style="DialogFieldTitle.TLabel").grid(
            row=5, column=0, sticky="w", padx=(0, 12), pady=(12, 0)
        )
        ttk.Label(frame, textvariable=tool_detail_var, style="PanelMuted.TLabel", wraplength=450).grid(
            row=5, column=1, sticky="w", pady=(12, 0)
        )

        def update_color_preview(_event: object | None = None) -> None:
            color_preview.configure(bg=ACCENT_CHOICES.get(color_var.get(), ACCENT_CHOICES["Xanh ngọc"]))

        def set_custom_fields_enabled(enabled: bool) -> None:
            entry_state = "normal" if enabled else "disabled"
            title_entry.configure(state=entry_state)
            description_entry.configure(state=entry_state)
            color_combo.configure(state="readonly" if enabled else "disabled")

        def refresh_tool_detail(_event: object | None = None) -> None:
            tool_name, is_custom = tool_labels[selected_tool_label.get()]
            if is_custom:
                metadata = self.custom_tools[tool_name]
                title_var.set(metadata["title"])
                description_var.set(metadata.get("description", ""))
                color_var.set(color_name_for_hex(metadata.get("accent", ACCENT_CHOICES["Xanh ngọc"])))
                set_custom_fields_enabled(True)
                run_text, _run_bg, _run_fg = TOOL_STATUS_COLORS[self._tool_status_kind(tool_name, custom=True)]
                embedded_text, _embedded_bg, _embedded_fg = EMBEDDED_STATUS_COLORS[
                    self._embedded_status_kind(tool_name, custom=True)
                ]
                detail = f"File: {Path(metadata['script']).name}  |  {run_text}  |  {embedded_text}"
            else:
                metadata = TOOL_FILES[tool_name]
                title_var.set(metadata["title"])
                description_var.set(metadata["description"])
                color_var.set(color_name_for_hex(TOOL_ACCENTS.get(tool_name, ACCENT_CHOICES["Xanh ngọc"])))
                set_custom_fields_enabled(False)
                run_text, _run_bg, _run_fg = TOOL_STATUS_COLORS[self._tool_status_kind(tool_name)]
                embedded_text, _embedded_bg, _embedded_fg = EMBEDDED_STATUS_COLORS[
                    self._embedded_status_kind(tool_name)
                ]
                override = load_default_tool_overrides().get(tool_name) or {}
                override_source = override.get("source_name", "").strip()
                file_label = f"File chuẩn: {metadata['script']}"
                if override_source and override_source != metadata["script"]:
                    file_label += f"  (nguồn gốc: {override_source})"
                detail = f"{file_label}  |  {run_text}  |  {embedded_text}"
            tool_detail_var.set(detail)
            update_color_preview()

        def on_tool_selected(_event: object | None = None) -> None:
            selected_path["path"] = None
            selected_file.set("Chưa chọn file .py")
            refresh_tool_detail()

        combo.bind("<<ComboboxSelected>>", on_tool_selected)
        color_combo.bind("<<ComboboxSelected>>", update_color_preview)
        refresh_tool_detail()

        def choose_file() -> None:
            path = filedialog.askopenfilename(
                parent=dialog,
                title="Chọn file Python mới cho tool",
                filetypes=[("Python file", "*.py"), ("Tất cả file", "*.*")],
            )
            if path:
                selected_path["path"] = Path(path)
                selected_file.set(path)

        def apply_change() -> None:
            tool_name, is_custom = tool_labels[selected_tool_label.get()]
            source_path = selected_path["path"]
            if source_path is None:
                messagebox.showwarning("Thiếu file", "Hãy chọn một file .py trước.", parent=dialog)
                return
            try:
                if is_custom:
                    target_path = self._update_custom_tool(
                        tool_name,
                        title=title_var.get(),
                        description=description_var.get(),
                        accent=ACCENT_CHOICES.get(color_var.get(), ACCENT_CHOICES["Xanh ngọc"]),
                        source_path=source_path,
                    )
                    self.custom_tools = load_custom_tools()
                    updated_title = self.custom_tools[tool_name]["title"]
                    reloaded_payload = self.custom_tools[tool_name].get("payload", "").strip()
                    expected_payload = encode_embedded_tool_source(source_path.read_bytes())
                    if reloaded_payload != expected_payload:
                        messagebox.showerror(
                            "Lỗi nhúng tool",
                            f"File đã copy nhưng bản nhúng không khớp. Hãy thử lại.\nTool: {updated_title}",
                            parent=dialog,
                        )
                        return
                    short_source = f"...\\{source_path.parent.name}\\{source_path.name}"
                    self._styled_info(
                        "Đã cập nhật",
                        (
                            f"Tool: {updated_title}\n"
                            f"Thay thế: {target_path.name}\n"
                            f"Bằng: {short_source}"
                        ),
                        parent=dialog,
                    )
                    self.status_var.set(f"Đã cập nhật tool và nhúng thành công: {updated_title}.")
                    dialog.destroy()
                    self._build_dashboard_screen()
                    return
                replace_result = self._replace_tool_source(tool_name, source_path)
            except Exception as error:  # noqa: BLE001 - user-facing file replacement path.
                messagebox.showerror("Không cập nhật được tool", str(error), parent=dialog)
                return
            target_path = Path(str(replace_result["target_path"]))
            external_changed = bool(replace_result["external_changed"])
            embedded_changed = bool(replace_result["embedded_changed"])
            if external_changed and embedded_changed:
                detail_text = "Đã đổi file tool và cập nhật bản dự phòng nhúng."
            elif external_changed:
                detail_text = "Đã đổi file tool. Bản nhúng đã trùng sẵn với file mới."
            elif embedded_changed:
                detail_text = "File đang dùng đã trùng sẵn. Đã đồng bộ lại bản dự phòng nhúng."
            else:
                detail_text = "File đã chọn trùng hoàn toàn với file đang dùng. Không có thay đổi nội dung."
            short_source = f"...\\{source_path.parent.name}\\{source_path.name}"
            self.status_var.set(f"Đã cập nhật {TOOL_FILES[tool_name]['title']}: {target_path.name} ← {source_path.name}")
            refresh_tool_detail()
            self._styled_info(
                "Đã cập nhật",
                (
                    f"Tool: {TOOL_FILES[tool_name]['title']}\n"
                    f"Thay thế: {target_path.name}\n"
                    f"Bằng: {short_source}"
                ),
                parent=dialog,
            )
            dialog.destroy()

        button_row = ttk.Frame(shell, style="App.TFrame")
        button_row.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        button_row.columnconfigure(1, weight=1)
        self._flat_button(button_row, text="Chọn file .py", command=choose_file).grid(row=0, column=0, sticky="w")
        self._flat_button(button_row, text="Cập nhật", command=apply_change, primary=True).grid(
            row=0, column=2, padx=(8, 0)
        )
        self._flat_button(button_row, text="Đóng", command=dialog.destroy).grid(row=0, column=3, padx=(8, 0))

        dialog.update_idletasks()
        x = self.root.winfo_rootx() + max((self.root.winfo_width() - dialog.winfo_width()) // 2, 0)
        y = self.root.winfo_rooty() + max((self.root.winfo_height() - dialog.winfo_height()) // 3, 0)
        dialog.geometry(f"+{x}+{y}")

    def _replace_tool_source(self, tool_name: str, source_path: Path) -> dict[str, object]:
        """Replace one logical tool source and verify both file and embedded snapshot."""

        metadata = TOOL_FILES.get(tool_name)
        if metadata is None:
            raise ValueError(f"Tool không hợp lệ: {tool_name}")
        if not source_path.exists() or not source_path.is_file():
            raise FileNotFoundError(f"Không tìm thấy file đã chọn: {source_path}")
        if source_path.suffix.lower() != ".py":
            raise ValueError("Chỉ hỗ trợ đổi bằng file .py.")

        source_bytes = source_path.read_bytes()
        compile(source_bytes, str(source_path), "exec")
        if is_launcher_source(source_bytes):
            raise ValueError(
                "File đã chọn chỉ là launcher (cần thư mục package nằm cạnh). "
                "Hãy chép cả thư mục tool mới vào thư mục tool thay vì đổi bằng file này."
            )
        source_hash = sha256_hex(source_bytes)
        expected_payload = encode_embedded_tool_source(source_bytes)

        target_path = tool_workspace_dir() / metadata["script"]
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_before = target_path.read_bytes() if target_path.exists() else b""
        target_before_hash = sha256_hex(target_before) if target_before else ""
        embedded_before_payload = default_tool_payload(tool_name)
        embedded_before_hash = sha256_hex(embedded_before_payload.encode("ascii")) if embedded_before_payload else ""

        try:
            same_file = source_path.resolve() == target_path.resolve()
        except OSError:
            same_file = False
        external_changed = target_before != source_bytes
        if not same_file and external_changed:
            if target_path.exists():
                backup_dir = target_path.parent / "tool_backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_path = backup_dir / f"{target_path.stem}_{time.strftime('%Y%m%d_%H%M%S')}{target_path.suffix}.bak"
                shutil.copy2(target_path, backup_path)
            shutil.copy2(source_path, target_path)

        if getattr(sys, "frozen", False):
            overrides = load_default_tool_overrides()
            overrides[tool_name] = {
                "payload": expected_payload,
                "source_hash": source_hash,
                "source_name": source_path.name,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_default_tool_overrides(overrides)
        else:
            refresh_embedded_payloads_in_control_panel()
        target_after = target_path.read_bytes()
        if target_after != source_bytes:
            raise RuntimeError(
                "Đổi file chưa hoàn tất: file đang dùng sau cập nhật không khớp file đã chọn."
            )

        embedded_after_payload = default_tool_payload(tool_name)
        if embedded_after_payload != expected_payload:
            raise RuntimeError(
                "Đổi file chưa hoàn tất: bản nhúng dự phòng sau cập nhật không khớp file đã chọn."
            )

        target_after_hash = sha256_hex(target_after)
        embedded_after_hash = sha256_hex(embedded_after_payload.encode("ascii")) if embedded_after_payload else ""
        embedded_changed = embedded_before_payload != embedded_after_payload
        return {
            "target_path": target_path,
            "external_changed": external_changed,
            "embedded_changed": embedded_changed,
            "source_hash": source_hash,
            "target_before_hash": target_before_hash,
            "target_after_hash": target_after_hash,
            "embedded_before_hash": embedded_before_hash,
            "embedded_after_hash": embedded_after_hash,
        }

    def _add_custom_tool(self, title: str, description: str, accent: str, source_path: Path) -> str:
        """Add one user-selected Python file as a custom dashboard tool."""

        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Hãy nhập tên tool.")
        if not source_path.exists() or not source_path.is_file():
            raise FileNotFoundError(f"Không tìm thấy file đã chọn: {source_path}")
        if source_path.suffix.lower() != ".py":
            raise ValueError("Chỉ hỗ trợ thêm file .py.")

        source_bytes = source_path.read_bytes()
        compile(source_bytes, str(source_path), "exec")

        tool_id = custom_tool_id_for_title(clean_title)
        script_name = f"{tool_id}.py"
        relative_script = f"{CUSTOM_TOOLS_DIR_NAME}/{script_name}"
        target_path = tool_workspace_dir() / relative_script
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        tools = load_custom_tools()
        tools[tool_id] = {
            "title": clean_title,
            "description": description.strip() or "Tool tùy chỉnh.",
            "script": relative_script,
            "accent": accent if accent.startswith("#") else ACCENT_CHOICES["Xanh ngọc"],
            "payload": encode_embedded_tool_source(source_bytes),
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        save_custom_tools(tools)
        return tool_id

    def _update_custom_tool(
        self,
        tool_id: str,
        *,
        title: str,
        description: str,
        accent: str,
        source_path: Path | None = None,
    ) -> Path:
        """Update display metadata and optionally replace the source of a custom tool."""

        tools = load_custom_tools()
        metadata = tools.get(tool_id)
        if metadata is None:
            raise ValueError(f"Tool tùy chỉnh không tồn tại: {tool_id}")

        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Hãy nhập tên tool.")

        target_path = custom_tool_external_path(metadata)
        if source_path is not None:
            if not source_path.exists() or not source_path.is_file():
                raise FileNotFoundError(f"Không tìm thấy file đã chọn: {source_path}")
            if source_path.suffix.lower() != ".py":
                raise ValueError("Chỉ hỗ trợ file .py.")
            source_bytes = source_path.read_bytes()
            compile(source_bytes, str(source_path), "exec")
            expected_payload = encode_embedded_tool_source(source_bytes)

            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_before = target_path.read_bytes() if target_path.exists() else b""
            try:
                same_file = source_path.resolve() == target_path.resolve()
            except OSError:
                same_file = False
            if not same_file and target_before != source_bytes:
                if target_path.exists():
                    backup_dir = target_path.parent / "tool_backups"
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    backup_path = backup_dir / f"{target_path.stem}_{time.strftime('%Y%m%d_%H%M%S')}.py.bak"
                    shutil.copy2(target_path, backup_path)
                shutil.copy2(source_path, target_path)
            if target_path.read_bytes() != source_bytes:
                raise RuntimeError("Cập nhật tool tùy chỉnh chưa hoàn tất: file đang dùng không khớp file đã chọn.")
            metadata["payload"] = expected_payload

        metadata["title"] = clean_title
        metadata["description"] = description.strip() or "Tool tùy chỉnh."
        metadata["accent"] = accent if accent.startswith("#") else ACCENT_CHOICES["Xanh ngọc"]
        metadata["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tools[tool_id] = metadata
        save_custom_tools(tools)
        return target_path
