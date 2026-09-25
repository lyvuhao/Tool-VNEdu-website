"""Điểm khởi chạy của tool KHDH."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .ui.styles import apply_wizard_styles
from .ui.theme import CLR_PANEL_BG, fit_geometry_to_work_area, get_tk_work_area
from .ui.wizard.wizard import KHDHWizard


# =====================================================================
# Standalone runner
# =====================================================================

def main():
    root = tk.Tk()
    root.withdraw()
    root.title("KHDH tự động")
    root.configure(background=CLR_PANEL_BG)
    apply_wizard_styles(ttk.Style(root))
    target_w, target_h, x, y = fit_geometry_to_work_area(
        root,
        preferred_w=1280,
        preferred_h=950,
        margin=10,
        min_w=900,
        min_h=560,
        anchor="top_center",
    )
    root.geometry(f"{target_w}x{target_h}+{x}+{y}")
    root.minsize(min(1180, target_w), min(760, target_h))
    _, _, work_w, work_h = get_tk_work_area(root)
    root.maxsize(work_w, work_h)

    wizard = KHDHWizard(root)
    wizard.pack(fill="both", expand=True)
    root.update_idletasks()
    root.deiconify()
    root.lift()
    root.mainloop()
