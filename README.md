# Tool-VNEdu-website

Bộ công cụ tự động hoá VNEDU (Tkinter + Playwright/Chrome CDP). Mọi mã nguồn nằm trong thư mục
`Tool VnEdu ChatLuong/`.

## Cách chạy

Tên file và lệnh chạy **giữ nguyên như trước**. Mỗi file `.py` ở thư mục gốc giờ chỉ là *launcher*
mỏng, gọi vào package tương ứng:

| Launcher (chạy như cũ)      | Package          | Chức năng                                   | Chạy dạng module             |
|-----------------------------|------------------|---------------------------------------------|------------------------------|
| `nhanxet_pro.py`            | `nhanxet/`       | Ghi nhận xét vào Sổ điểm                    | `python -m nhanxet`          |
| `nhapdiem_pro.py`           | `nhapdiem/`      | Nhập điểm bằng giọng nói                    | `python -m nhapdiem`         |
| `auto_khbd_pro.py`          | `auto_khbd/`     | Nhập Kế hoạch dạy học (KHDH)                | `python -m auto_khbd`        |
| `auto_SĐB.py`               | `auto_sdb/`      | Nhập Sổ đầu bài qua Chrome CDP              | `python -m auto_sdb`         |
| `vnedu_control_panel2.py`   | `control_panel/` | Dashboard mở các tool                       | `python -m control_panel`    |

```bash
cd "Tool VnEdu ChatLuong"
python nhapdiem_pro.py            # như trước
python nhapdiem_pro.py --self-test
python vnedu_control_panel2.py --self-test
```

Thư viện cần có: `playwright`. Tuỳ chọn: `openpyxl`, `python-docx` (KHDH);
`numpy`, `sounddevice`, `SpeechRecognition`, `rapidfuzz`, `requests`, `soundfile` (nhập điểm giọng nói).

File cấu hình/dữ liệu (`*_config.json`, cache, alias…) **vẫn nằm cạnh launcher** như trước, nên cấu hình
cũ tiếp tục dùng được. Mỗi package có `paths.TOOL_DIR` trỏ về thư mục này.

## Cấu trúc

Mỗi package có `__init__.py` chứa **mục lục module** (tên file + mô tả một dòng). Muốn biết một chức năng
nằm ở đâu thì mở `__init__.py` của package đó.

```
Tool VnEdu ChatLuong/
├── nhanxet/                 Ghi nhận xét
│   ├── config.py, models.py, progress.py, access.py, rules.py, write_plan.py
│   ├── automation/          VnEduScoreAutomation (browser, login, snapshot, combo, schema, comment_write, ...)
│   └── ui/                  AutoNhanXetV2App (layout, progress, columns, rules, apply, ...)
├── nhapdiem/                Nhập điểm giọng nói
│   ├── scorebook_core/      Lõi Sổ điểm: KẾ THỪA nhanxet, chỉ chứa phần khác/mới (ghi điểm, xác minh lưu)
│   ├── automation/          Quyền nhập điểm, quét lớp/môn, lấy nhanh dữ liệu điểm
│   ├── voice/               Thu âm, Google Speech, phiên âm tên (Việt/Khmer), tách tên + điểm
│   ├── ui/                  VnEduStandaloneApp (layout, bảng điểm, alias, PTT, khớp giọng nói, ...)
│   └── scores.py, matching.py, storage.py, models.py, config.py, compat.py, selftest.py
├── auto_khbd/               KHDH
│   ├── engine/              Nghiệp vụ, không phụ thuộc giao diện
│   │   ├── parser.py, catalog.py, planner.py, bootstrap.py, fallback_log.py
│   │   ├── analyzer/  client/  profile/  executor/  backup/
│   └── ui/                  theme, styles, workers/, dialogs/, wizard/, full_tkb_editor/, backup_restore/
├── auto_sdb/                Sổ đầu bài
│   ├── cdp/                 ChromeBridge (connection, navigation, dropdowns, table, form_fill, form_save, ...)
│   └── app/                 AutoDaNangApp (schedule_*, class_stats_*, delete_dialog, chrome, window, ...)
└── control_panel/           Dashboard
    ├── embedded_payloads.py Mã tool dự phòng (gzip+base64) — được ghi đè tự động, không sửa tay
    ├── storage.py, custom_tools.py, embedded.py, process.py, login.py, ...
    └── ui/                  ControlPanelApp (widgets, health, dashboard, screens, tool_dialogs, ...)
```

### Lớp lớn = ghép từ nhiều mixin

Các lớp khổng lồ trước đây (ví dụ `ChromeBridge` ~8.500 dòng, `AutoDaNangApp` ~10.200 dòng,
`KHDHWizard` ~5.000 dòng) được chia theo nhóm chức năng thành các **mixin**, mỗi mixin nằm trong một file:

```python
# auto_sdb/cdp/bridge.py
class ChromeBridge(ConnectionMixin, NavigationMixin, DropdownMixin, ..., PageUtilsMixin):
    def __init__(self, ...): ...
```

Code gọi vẫn giữ nguyên (`bridge.fill_form(...)`, `self._log(...)`). Khi cần sửa hoặc debug một chức
năng thì mở đúng file mixin: ví dụ lỗi điền form Sổ đầu bài → `auto_sdb/cdp/form_fill.py`, lỗi lưu →
`auto_sdb/cdp/form_save.py`. Traceback giờ cũng chỉ thẳng tới file nhỏ tương ứng.

## Những gì đã refactor

1. **Tách module**: 5 file (~74.000 dòng) thành 5 package, 263 file `.py`, phần lớn dưới 800 dòng.
   Nội dung từng hàm/phương thức được chuyển **nguyên văn**; import được sinh tự động theo tên thực sự dùng.
2. **Bỏ bản sao nhúng trong `nhapdiem_pro.py`**: trước đây file này chứa nguyên một bản `nhanxet_pro.py`
   (đã chỉnh sửa) dạng chuỗi và chạy bằng `exec()`. Giờ `nhapdiem/scorebook_core` import phần giống hệt
   từ `nhanxet` và chỉ giữ 15 phương thức khác + 16 phương thức mới. GUI nhận xét nằm trong bản nhúng
   không được dùng (code chết), nên đã bỏ.
3. **Sửa lỗi** trong control panel: khi đăng nhập hoặc mở lại VNEDU thất bại, lambda dùng biến `error`
   của khối `except` sau khi Python đã xoá biến đó, gây `NameError` thay vì hiện thông báo lỗi. Đã sửa
   bằng `lambda error=error: ...`.
4. **Dọn code**: bỏ import thừa, biến tính ra mà không dùng (khi vế phải không có tác dụng phụ), f-string
   không có placeholder; mixin gọi staticmethod của chính nó qua tên mixin (tránh import vòng).
5. `nhapdiem`: `--config-file` giờ ghi vào `nhapdiem.config.CONFIG_FILE` (thay cho `global`) để mọi
   module thấy giá trị mới.

### Đã kiểm chứng

- Đối chiếu AST: mọi hàm/phương thức của bản gốc có mặt đúng một lần, nội dung không đổi (trừ các chỗ
  sửa có chủ đích ở trên).
- Dựng giao diện thật (Xvfb) của bản gốc và bản mới: cây widget, thuộc tính và danh sách phương thức
  **giống hệt** ở tất cả tool và các cửa sổ phụ chính.
- `nhapdiem --self-test` và `control panel --self-test`: PASS, output giống bản gốc.
- Lõi Sổ điểm dùng chung: cả 95 phương thức của `VnEduScoreAutomation` (bản nhập điểm) cho ra mã giống
  bản nhúng cũ (chỉ khác 2 biến thừa đã bỏ trong `_detect_scorebook_columns`).
- Launcher chạy được từ thư mục bất kỳ; `python -m <package>` chạy được.

## Còn để ngỏ (nên xem thêm)

- Một số phương thức rất dài vẫn là một khối, vì tách tiếp cần viết lại logic và phải test trên web thật:
  `ChromeBridge.fill_form` (~800 dòng), `fetch_sodaubai_rows(_bulk)` (~570 dòng mỗi hàm),
  `AutoDaNangApp._schedule_worker_khdh` (~800), `PlanExecutor._execute_week` (~740).
- `nhanxet/automation` và `nhapdiem/scorebook_core` vẫn là hai phiên bản khác nhau của 15 phương thức.
  Có thể hợp nhất nếu bản của Nhập điểm cũng đúng cho luồng Ghi nhận xét (cần test thực tế).
- Code có vẻ làm dở mà pyflakes chỉ ra, được giữ nguyên để không đổi giao diện/hành vi:
  - 3 `ttk.Label` được tạo nhưng không đặt lên màn hình (`nhapdiem/ui/layout.py`);
  - `detail_text` được tính nhưng không hiển thị (`control_panel/ui/tool_dialogs.py`);
  - `expected_score_pairs` (`nhapdiem/scorebook_core/automation/payload_write.py`).
- `control_panel/tool_registry.py` vẫn trỏ tới tên script cũ (`nhapdiem21.py`, `auto_danang8.py`,
  `locdiem.py`); nếu không có file đó, control panel dùng bản nhúng dự phòng như trước.
