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
python -m unittest discover -s tests    # test logic (không cần trình duyệt)
```

Thư viện cần có: `playwright`. Tuỳ chọn: `openpyxl` (KHDH, nhập điểm từ file Excel), `python-docx` (KHDH);
`numpy`, `sounddevice`, `SpeechRecognition`, `rapidfuzz`, `requests`, `soundfile` (nhập điểm giọng nói).

File cấu hình/dữ liệu (`*_config.json`, cache, alias…) **vẫn nằm cạnh launcher** như trước, nên cấu hình
cũ tiếp tục dùng được. Mỗi package có `paths.TOOL_DIR` trỏ về thư mục này.

## Tính năng mới

### Nhập điểm từ file Excel/CSV (tool Nhập điểm)

1. Đăng nhập, chọn lớp/môn, chọn **Cột điểm đích** để tool quét danh sách học sinh như bình thường.
2. Bấm **📄 NHẬP TỪ EXCEL**, chọn file `.xlsx`/`.xlsm`/`.csv`.
3. Hộp thoại xem trước tự đoán sheet, cột họ tên (kể cả họ/tên tách hai cột), cột mã HS và cột điểm.
   Cột nào đoán sai thì chọn lại, bảng khớp cập nhật ngay.
4. Kết quả khớp mỗi dòng:
   - **Xanh**: khớp chắc chắn (theo mã HS, theo họ tên, hoặc theo họ tên bỏ dấu); được chọn sẵn.
   - **Vàng**: cần kiểm tra (trùng tên trong lớp, hoặc tên gần đúng do sai chính tả); mặc định **không** chọn.
   - **Đỏ / xám**: không khớp, điểm lỗi, ô trống, dòng trùng, hoặc trùng điểm đang có; bỏ qua.
5. Bấm **Đưa N điểm vào hàng chờ**. Điểm vào cột "Điểm chờ" (hoàn tác được bằng Ctrl+Z), sau đó bấm
   **GHI ĐIỂM LÊN WEB** như với điểm đọc bằng giọng nói. Phần ghi lên VNEDU không thay đổi.

File `.xls` đời cũ chưa hỗ trợ: mở bằng Excel rồi lưu lại dạng `.xlsx`. Đọc `.xlsx` cần thư viện `openpyxl`;
file CSV thì không cần.

### Nhiều câu nhận xét cho mỗi mức điểm (tool Ghi nhận xét)

- Trong ô "Mẫu nhận xét", ngăn các câu bằng dấu `|`, hoặc bấm nút **✎** để soạn mỗi câu một dòng.
- Tool chia đều các câu cho học sinh cùng mức điểm, và các bạn liền nhau nhận câu khác nhau.
- Học sinh đã có đúng một câu trong danh sách thì giữ nguyên, nên chạy lại không làm xáo trộn.
- Rule chỉ có một câu chạy y hệt trước đây. Bộ rule mặc định ("Nhận xét mặc định") nay có sẵn 2–3 câu
  cho mỗi mức; câu đầu tiên vẫn là câu cũ.

### Ghi log ra file (4 tool)

- Mỗi tool ghi log vào `Tool VnEdu ChatLuong/logs/<tên tool>.log`: `nhapdiem`, `nhanxet`, `auto_khbd`,
  `auto_sdb`. Log xoay vòng, tối đa 2 MB × 5 file.
- Log gồm các dòng hiện trên giao diện, cộng với **mọi lỗi chưa bắt kèm traceback đầy đủ** (luồng chính,
  thread nền, callback Tkinter). Trước đây các lỗi này mất hẳn khi chạy bằng `pythonw` hoặc `.exe`.
- Dashboard có mục **Công cụ → Mở thư mục log của tool**. Khi báo lỗi, gửi kèm file log là đủ.
- Log có thể chứa tên học sinh (giống nội dung trên giao diện). Đặt biến môi trường
  `VNEDU_DISABLE_FILE_LOG=1` để tắt ghi log.

### Control panel chạy code mới

- "Nhập điểm" → `nhapdiem_pro.py`, "Sổ đầu bài" → `auto_SĐB.py`. Trước đây dashboard trỏ tới
  `nhapdiem21.py` / `auto_danang8.py` không có trong repo, nên luôn chạy bản nhúng **cũ hơn**.
- Launcher thiếu thư mục package bên cạnh thì tự dùng bản nhúng dự phòng (bản đơn file). Khi cập nhật bản
  nhúng, launcher không bị nhúng nhầm thay cho bản đơn file.
- Kiểm tra sức khoẻ tool và `--self-test` compile cả package. Bản `.exe` tự chép các package sang thư mục
  chạy tool.
- Dashboard có thêm thẻ **Kế hoạch dạy học** (chạy `auto_khbd_pro.py`, kèm bản nhúng dự phòng đơn file).
  Cửa sổ dashboard tự cao thêm để thấy đủ thẻ mặc định mà không phải cuộn.
- KHDH **nhận tài khoản từ dashboard**:
  - Dashboard ghi `khdh_config.json` (tài khoản, cổng Chrome, URL trường — **không có mật khẩu**).
  - KHDH điền sẵn tài khoản và dùng đúng cổng/URL đó.
  - Nếu Chrome đã đăng nhập VNEDU (từ dashboard) thì bấm **Đăng nhập VnEdu** luôn, không cần gõ mật khẩu.
  - Nếu phiên chưa đăng nhập hoặc đã hết hạn, KHDH báo "Cần mật khẩu" (không gửi form rỗng).
  - Đăng nhập thành công thì KHDH nhớ tài khoản cho lần sau (vẫn không lưu mật khẩu).
- Bản nhúng dự phòng được nén ổn định (cùng nội dung → cùng chuỗi), nên cập nhật bản nhúng không tạo thay
  đổi thừa trong git.

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
├── vnedu_common/            Dùng chung: logging_setup (log ra file, bắt lỗi chưa xử lý)
├── tests/                   Test unittest: nhận xét nhiều câu, nhập điểm từ file, fill_form (ExtJS giả),
│                            worker KHDH (trình duyệt giả)
└── control_panel/           Dashboard
    ├── embedded_payloads.py Mã tool dự phòng (gzip+base64) — được ghi đè tự động, không sửa tay
    ├── storage.py, custom_tools.py, embedded.py, process.py, login.py, ...
    └── ui/                  ControlPanelApp (widgets, health, dashboard, screens, tool_dialogs, ...)
```

### `fill_form` (Sổ đầu bài) — điền popup "Chi tiết tiết học"

`auto_sdb/cdp/form_fill.py` giờ chỉ điều phối các bước; mỗi bước là một hàm riêng:

| Bước | Hàm | Việc làm |
|---|---|---|
| 1 | `_mon_hoc_field_candidates` | Tên field Môn học có thể có |
| 2 | `_prime_popup_combo_stores` / `_wait_popup_combo_stores` | Expand/bindStore combobox lazy-load, chờ store nạp |
| 3 | `_build_fill_form_payload` | Gom tham số gửi xuống JS |
| 4 | `JS_FILL_POPUP_FORM` | Điền + xác minh từng field trong trình duyệt |
| 5 | `_finish_fill_form` | Tên bài tự điền theo PPCT, dựng thông điệp kết quả |

Phần JavaScript (~600 dòng) nằm ở `auto_sdb/cdp/form_fill_js.py`, chia theo nhóm hàm: tìm popup, đọc store,
tìm field/record, chờ store, set combobox / ô thường, luồng chính. Chuỗi ghép lại giống hệt từng ký tự bản
cũ. `tests/test_fill_form.py` chạy `fill_form` trên trang ExtJS giả (`tests/fixtures/fake_extjs.js`)
bằng Chromium; đặt `VNEDU_TEST_CHROMIUM=<đường dẫn chrome>` nếu Playwright chưa cài trình duyệt.

### Worker nhập Sổ đầu bài theo KHDH (`_schedule_worker_khdh`)

`auto_sdb/app/schedule_worker_khdh.py`: lớp `KhdhScheduleJob` giữ trạng thái một lần chạy (bộ đếm,
checkpoint để chạy tiếp) thay cho các hàm lồng dùng `nonlocal`:

```
run()                      kết nối CDP -> _process_all_weeks() -> _finish() (dọn dẹp + gửi "done")
  _process_week()          chọn tuần -> lọc lớp -> chọn lớp -> bật "Gợi ý theo KHDH" -> đọc hàng đỏ
    _process_rows()        từng hàng đỏ, cập nhật checkpoint "hàng kế tiếp"
      _process_row()       ROW_SKIP / ROW_STOP / ROW_DONE
        _open_row_form()          bấm "+", chờ popup
        _read_verified_popup()    đọc popup, dừng nếu popup mở sai hàng
        _fill_row_form()          điền tối thiểu hoặc điền đủ từ gợi ý hàng đỏ
        _save_and_confirm_row()   lưu, xác nhận trên bảng, dừng nếu lưu mơ hồ
```

Hàm thuần (so popup với hàng đỏ, khoá resume, chọn dữ liệu điền…) nằm ở `auto_sdb/app/khdh_rows.py`.
Đã fuzz so sánh bản cũ/mới với trình duyệt giả: hơn 2.000 kịch bản (lỗi chọn tuần/lớp, Chrome đóng,
popup sai hàng, lưu mơ hồ, exception, người dùng dừng, chạy tiếp…) cho cùng chuỗi sự kiện và cùng chuỗi
lệnh gọi trình duyệt. Test: `tests/test_khdh_worker.py`.

**Sửa lỗi chỉ số 0:** bản cũ đọc chỉ số hàng/nút "+" bằng `int(x or -1)`, nên chỉ số 0 bị đổi thành -1 và
nhánh dự phòng không bấm được nút "+" đầu tiên của tuần. Nay dùng `khdh_rows.as_index()` (giữ đúng 0).
Fuzz lại: không có chỉ số 0 thì bản cũ/mới giống hệt; có chỉ số 0 thì khác biệt duy nhất là tham số
`click_add_button` (-1 -> 0). Cùng lỗi ở Nhập điểm (`targetLeafIndex` = 0 làm lấy điểm nhanh rơi về quét
chậm) cũng đã sửa trong `nhapdiem/ui/scorebook.py`.

### Worker nhập Sổ đầu bài theo lịch — thủ công (`_schedule_worker`)

`auto_sdb/app/schedule_job.py`: lớp `ScheduleJob` (cùng kiểu với `KhdhScheduleJob`), `_schedule_worker`
chỉ còn gọi `ScheduleJob(app, params).run()`:

```
run()                        kết nối CDP -> _process_all_weeks() -> _finish() (dọn dẹp + gửi "done")
  _process_week()            chọn tuần -> chọn lớp -> đọc bảng tuần (WeekRowCache)
    _process_slots()         từng slot trong lịch, cập nhật checkpoint "slot kế tiếp"
      _process_slot()        SLOT_DONE / SLOT_SKIP
        _resolve_slot_row()       tìm hàng của slot, đọc lại bảng nếu cần
        _handle_existing_row()    hàng đã có dữ liệu -> bỏ qua, đồng bộ PPCT
        _open_slot_form()         bấm "+", chờ form
        _fill_slot_form()         điền PPCT, HS nghỉ, nhận xét, điểm, môn/phân môn
        _save_and_confirm_slot()  lưu, xác nhận trên bảng, dừng nếu lưu mơ hồ
```

Fuzz so sánh với bản cũ: 1.400 kịch bản giống hệt (cùng chuỗi sự kiện và lệnh gọi trình duyệt), phủ mọi
kết quả slot. Test: `tests/test_schedule_worker.py`.

**Sửa lỗi "dừng mà không dừng":** khi worker tự dừng giữa tuần (lưu mơ hồ, hàng đã có dữ liệu mà không đọc
được PPCT) hoặc người dùng bấm dừng, bản cũ chỉ thoát vòng slot rồi vẫn sang tuần sau: checkpoint bị ghi đè
thành "tuần sau, slot 0" (chạy tiếp sẽ bỏ sót các slot còn lại của tuần đang dở), và với dừng do lỗi thì còn
**lưu thêm** slot đầu của mỗi tuần sau. Nay dừng hẳn, checkpoint giữ đúng slot đang dở. Fuzz sau khi sửa:
kịch bản không dừng giữa chừng vẫn giống hệt bản cũ; kịch bản có dừng thì bản mới là phần đầu của bản cũ
(dừng sớm hơn), không ghi gì thêm.

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
  `fetch_sodaubai_rows(_bulk)` (~570 dòng mỗi hàm), `PlanExecutor._execute_week` (~740).
  `ChromeBridge.fill_form`, `_schedule_worker_khdh` và `_schedule_worker` đã được tách (xem ở trên).
- `nhanxet/automation` và `nhapdiem/scorebook_core` vẫn là hai phiên bản khác nhau của 15 phương thức.
  Có thể hợp nhất nếu bản của Nhập điểm cũng đúng cho luồng Ghi nhận xét (cần test thực tế).
- Code có vẻ làm dở mà pyflakes chỉ ra, được giữ nguyên để không đổi giao diện/hành vi:
  - 3 `ttk.Label` được tạo nhưng không đặt lên màn hình (`nhapdiem/ui/layout.py`);
  - `detail_text` được tính nhưng không hiển thị (`control_panel/ui/tool_dialogs.py`);
  - `expected_score_pairs` (`nhapdiem/scorebook_core/automation/payload_write.py`).
- "Lọc học lực" (`locdiem.py`) không có trong repo nên dashboard vẫn chạy bản nhúng của nó.
