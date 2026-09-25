"""Style ttk cho wizard."""

from __future__ import annotations

from tkinter import ttk

from .theme import (
    CLR_BODY,
    CLR_BORDER,
    CLR_ERR,
    CLR_HINT,
    CLR_LINK,
    CLR_NAVY,
    CLR_OK,
    CLR_PANEL_BG,
    CLR_SUBTLE,
    CLR_WARN,
)


# =====================================================================
# Style helper
# =====================================================================

def apply_wizard_styles(style: ttk.Style) -> None:
    """Áp style ttk cho wizard. Idempotent."""
    try:
        style.theme_use("clam")
    except Exception:
        pass

    # Buttons
    style.configure(
        "WizPrimary.TButton",
        background="#2563eb", foreground="#ffffff",
        padding=(14, 8), borderwidth=1,
        font=("Segoe UI", 10, "bold"),
    )
    style.map(
        "WizPrimary.TButton",
        background=[("disabled", "#bfdbfe"), ("pressed", "#1d4ed8"),
                    ("active", "#3b82f6")],
        foreground=[("disabled", "#eff6ff")],
    )

    style.configure(
        "WizSuccess.TButton",
        background="#16a34a", foreground="#ffffff",
        padding=(14, 8), borderwidth=1,
        font=("Segoe UI", 10, "bold"),
    )
    style.map(
        "WizSuccess.TButton",
        background=[("disabled", "#bbf7d0"), ("pressed", "#15803d"),
                    ("active", "#22c55e")],
        foreground=[("disabled", "#f0fdf4")],
    )

    style.configure(
        "WizDanger.TButton",
        background="#fee2e2", foreground="#7f1d1d",
        padding=(12, 7), borderwidth=1, font=("Segoe UI", 10, "bold"),
    )
    style.map(
        "WizDanger.TButton",
        background=[("disabled", "#f5e8e8"), ("pressed", "#fecaca"),
                    ("active", "#fee2e2")],
        foreground=[("disabled", "#a8a29e")],
    )

    style.configure(
        "WizSubtle.TButton",
        background="#f9fafb", foreground="#374151",
        padding=(12, 7), borderwidth=1, font=("Segoe UI", 10),
    )
    style.map(
        "WizSubtle.TButton",
        background=[("disabled", "#f3f4f6"), ("pressed", "#e5e7eb"),
                    ("active", "#eef2ff")],
        foreground=[("disabled", "#9ca3af")],
    )

    # Progress bar
    style.configure(
        "WizBlue.Horizontal.TProgressbar",
        troughcolor="#e8edf3", background="#2f7adf",
        darkcolor="#1f5db8", lightcolor="#5a98e8",
        bordercolor="#cdd6e0", thickness=16,
    )

    # LabelFrame
    style.configure(
        "Wiz.TLabelframe",
        background=CLR_PANEL_BG, bordercolor=CLR_BORDER,
        relief="solid", borderwidth=1,
    )
    style.configure(
        "Wiz.TLabelframe.Label",
        background=CLR_PANEL_BG, foreground=CLR_NAVY,
        font=("Segoe UI", 10, "bold"),
    )

    # Frame
    style.configure("Wiz.TFrame", background=CLR_PANEL_BG)
    style.configure("WizToolbar.TFrame", background=CLR_PANEL_BG)

    # Labels
    style.configure("Wiz.TLabel", background=CLR_PANEL_BG, foreground=CLR_BODY,
                  font=("Segoe UI", 10))
    style.configure("WizTitle.TLabel", background=CLR_PANEL_BG, foreground=CLR_NAVY,
                  font=("Segoe UI", 16, "bold"))
    style.configure("WizSubtitle.TLabel", background=CLR_PANEL_BG, foreground=CLR_SUBTLE,
                  font=("Segoe UI", 10))
    style.configure("WizHint.TLabel", background=CLR_PANEL_BG, foreground=CLR_HINT,
                  font=("Segoe UI", 10))
    style.configure("WizMicro.TLabel", background=CLR_PANEL_BG, foreground=CLR_HINT,
                  font=("Segoe UI", 9))
    style.configure("WizAccent.TLabel", background=CLR_PANEL_BG, foreground=CLR_LINK,
                  font=("Segoe UI", 10, "bold"))
    style.configure("WizOk.TLabel", background=CLR_PANEL_BG, foreground=CLR_OK,
                  font=("Segoe UI", 10, "bold"))
    style.configure("WizWarn.TLabel", background=CLR_PANEL_BG, foreground=CLR_WARN,
                  font=("Segoe UI", 10, "bold"))
    style.configure("WizErr.TLabel", background=CLR_PANEL_BG, foreground=CLR_ERR,
                  font=("Segoe UI", 10, "bold"))

    # Treeview
    style.configure(
        "Wiz.Treeview",
        background="#ffffff", fieldbackground="#ffffff",
        foreground=CLR_BODY, rowheight=30,
        font=("Segoe UI", 10), borderwidth=1,
    )
    style.configure(
        "Wiz.Treeview.Heading",
        background="#e7eef5", foreground=CLR_NAVY,
        font=("Segoe UI", 9, "bold"), padding=(6, 4),
    )
    style.map(
        "Wiz.Treeview",
        background=[("selected", "#cfe5f6")],
        foreground=[("selected", "#0c2c4d")],
    )
