"""Nhập điểm từ file Excel/CSV (nhapdiem.excel_import)."""

import os
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nhapdiem import excel_import as X  # noqa: E402
from nhapdiem.models import ScoreStudentRow  # noqa: E402

try:
    from openpyxl import Workbook
except ImportError:  # pragma: no cover
    Workbook = None


def student(i, name, code="", current="", pending=""):
    return ScoreStudentRow(row_key=f"k{i}", row_index=i, row_id=str(i), student_code=code, student_name=name,
                           current_score=current, target_input_name=f"in{i}", pending_score=pending)


STUDENTS = [
    student(0, "Nguyễn Văn An", "HS001"), student(1, "Trần Thị Bình", "HS002", current="7"),
    student(2, "Lê Văn Cường", "HS003"), student(3, "Nguyễn Văn An", "HS004"),
    student(4, "Phạm Thị Dung", "HS005", pending="6"), student(5, "Hoàng Minh Đức", "HS006"),
    student(6, "Nguyễn Văn Ân", "HS007"),
]


def encode_cp1258(text):
    """Mã hoá như Excel "CSV (Windows tiếng Việt)": dấu thanh dạng tổ hợp."""
    out = b""
    for ch in text:
        try:
            out += ch.encode("cp1258")
            continue
        except UnicodeEncodeError:
            pass
        parts = unicodedata.normalize("NFD", ch)
        tones = "̣̀́̃̉"
        base = unicodedata.normalize("NFC", parts[0] + "".join(m for m in parts[1:] if m not in tones))
        out += (base + "".join(m for m in parts[1:] if m in tones)).encode("cp1258")
    return out


class ExcelImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def plan_for(self, path, **column_overrides):
        table = X.read_score_table(path)
        columns = X.guess_columns(table)
        for key, value in column_overrides.items():
            setattr(columns, key, value)
        return table, columns, {item.source_row: item for item in X.build_import_plan(table, columns, STUDENTS)}

    @unittest.skipIf(Workbook is None, "cần openpyxl")
    def test_vnedu_style_export(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["SỔ ĐIỂM LỚP 6A1"])
        ws.append([])
        ws.append(["STT", "Mã học sinh", "Họ và tên", "TX1", "TX2", "GK"])
        for row in [(1, "HS001", "Nguyễn Văn An", 8, 7.5, 9), (2, "HS002", "Trần Thị Bình", 7, 6, 8.25),
                    (3, "HS003", "Lê Văn Cường", None, 5, "abc"), (4, "HS004", "Nguyễn Văn An", 10, 9, 9),
                    (5, "HS005", "Phạm Thị Dung", 6.5, 7, 8), (6, "HS999", "Người Lạ", 5, 5, 5)]:
            ws.append(list(row))
        wb.save(self.dir / "a.xlsx")
        table, columns, by = self.plan_for(self.dir / "a.xlsx")
        self.assertEqual(table.header_row, 3)
        self.assertEqual((columns.name, columns.code, columns.score), (2, 1, 3))
        self.assertEqual((by[4].status, by[4].row_key, by[4].score, by[4].selected), (X.MATCH_CODE, "k0", "8", True))
        self.assertEqual(by[7].row_key, "k3")  # trùng tên nhưng khác mã
        self.assertEqual(by[5].status, X.SAME_AS_CURRENT)
        self.assertEqual(by[6].status, X.EMPTY_SCORE)
        self.assertIn("Thay điểm chờ 6", by[8].detail)
        self.assertEqual(by[9].status, X.NO_MATCH)
        _, _, by = self.plan_for(self.dir / "a.xlsx", score=5)
        self.assertEqual(by[5].score, "8.25")
        self.assertEqual(by[6].status, X.INVALID_SCORE)

    @unittest.skipIf(Workbook is None, "cần openpyxl")
    def test_split_names_duplicates_and_typos(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["TT", "Họ và tên đệm", "Tên", "Điểm KT"])
        for row in [(1, "Nguyễn Văn", "An", 8), (2, "Nguyen Van", "An", 9), (3, "Tran Thi", "Binh", 5),
                    (4, "Lê Văn", "Cường", 6), (5, "Lê Văn", "Cường", 7), (6, "Hoàng Minh", "Đứcc", 8),
                    (7, "Nguyễn Văn", "Ân", 4)]:
            ws.append(list(row))
        wb.save(self.dir / "b.xlsx")
        _, columns, by = self.plan_for(self.dir / "b.xlsx")
        self.assertEqual((columns.name, columns.given, columns.score), (1, 2, 3))
        self.assertEqual((by[2].status, by[2].row_key, by[2].selected), (X.MATCH_ORDER, "k0", False))
        self.assertEqual((by[3].status, by[3].row_key), (X.MATCH_ORDER, "k3"))
        self.assertEqual((by[4].status, by[4].row_key, by[4].selected), (X.MATCH_ASCII, "k1", True))
        self.assertEqual(by[6].status, X.DUPLICATE_SOURCE)
        self.assertEqual((by[7].status, by[7].row_key, by[7].selected), (X.MATCH_FUZZY, "k5", False))
        self.assertEqual((by[8].status, by[8].row_key), (X.MATCH_EXACT, "k6"))

    def test_csv_windows_vietnamese(self):
        (self.dir / "c.csv").write_bytes(encode_cp1258("STT;Họ tên học sinh;Điểm giữa kỳ\n1;Nguyễn Văn Ân;8,5\n"))
        table, _, by = self.plan_for(self.dir / "c.csv")
        self.assertEqual(table.headers[1], "Họ tên học sinh")
        self.assertEqual((by[2].row_key, by[2].score), ("k6", "8.5"))

    def test_csv_without_header(self):
        (self.dir / "d.csv").write_text("1,Lê Văn Cường,8\n2,Hoàng Minh Đức,9.5\n", encoding="utf-8")
        table, columns, by = self.plan_for(self.dir / "d.csv")
        self.assertEqual((table.header_row, columns.name, columns.score), (0, 1, 2))
        self.assertEqual([by[1].row_key, by[2].row_key, by[2].score], ["k2", "k5", "9.5"])

    def test_errors(self):
        (self.dir / "old.xls").write_bytes(b"x")
        with self.assertRaises(X.ImportFileError):
            X.read_score_table(self.dir / "old.xls")
        (self.dir / "e.csv").write_text("Họ và tên,Điểm\nLê Văn Cường,7\n", encoding="utf-8")
        table = X.read_score_table(self.dir / "e.csv")
        with self.assertRaises(X.ImportFileError):
            X.build_import_plan(table, X.ColumnChoice(name=0), STUDENTS)


if __name__ == "__main__":
    unittest.main()
