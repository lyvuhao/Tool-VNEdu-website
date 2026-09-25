"""Đăng nhập VNEDU qua Chrome debug."""

from __future__ import annotations

import importlib.util
import sys
from typing import Callable

from .embedded import resolve_tool_script


def login_to_vnedu(
    username: str,
    password: str,
    target_url: str,
    debug_port: int,
    progress_callback: Callable[[float, str], None] | None = None,
) -> str:
    """Open the configured VNEDU URL and perform login through existing automation."""

    import_errors: list[str] = []
    old_sys_path = sys.path[:]
    module_names = ("vnedu_login_nhapdiem", "vnedu_login_nhanxet")
    try:
        for tool_name, module_name, class_name in (
            ("nhapdiem", module_names[0], "VnEduScoreEntryAutomation"),
            ("nhanxet", module_names[1], "VnEduScoreAutomation"),
        ):
            try:
                script_path = resolve_tool_script(tool_name)
                sys.path.insert(0, str(script_path.parent))
                spec = importlib.util.spec_from_file_location(module_name, script_path)
                if spec is None or spec.loader is None:
                    raise ImportError(f"Không nạp được module từ {script_path}")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                automation_class = getattr(module, class_name)
                break
            except Exception as error:  # noqa: BLE001 - try the alternate automation source below.
                import_errors.append(f"{tool_name}: {error}")
        else:
            raise RuntimeError("Không nạp được automation đăng nhập VNEDU:\n" + "\n".join(import_errors))

        automation = automation_class(debug_port=debug_port, target_url=target_url)
        with automation._open_page() as page:  # Reuse the project automation contract.
            return automation._login_if_needed_on_page(
                page,
                username=username,
                password=password,
                progress_callback=progress_callback,
            )
    finally:
        sys.path = old_sys_path
        for module_name in module_names:
            sys.modules.pop(module_name, None)
