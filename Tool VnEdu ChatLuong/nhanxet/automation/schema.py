"""Nhận diện schema cột điểm/nhận xét từ header bảng."""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from ..models import ScorebookDetectedColumns, ScoreColumnSchema


class SchemaMixin:
    """Nhận diện schema cột điểm/nhận xét từ header bảng."""

    def _finalize_schema_identity(self, schemas: List[ScoreColumnSchema]) -> List[ScoreColumnSchema]:
        """Ensures repeated schema names and keys remain unique and stable."""
        name_counts: Dict[str, int] = {}
        name_totals: Dict[str, int] = {}
        key_counts: Dict[str, int] = {}
        key_totals: Dict[str, int] = {}
        for schema in schemas:
            base_name = schema.display_name.strip() or f"Cột {schema.leaf_index}"
            name_totals[base_name] = name_totals.get(base_name, 0) + 1
            base_key = schema.column_key.strip() or f"col_{schema.leaf_index}"
            key_totals[base_key] = key_totals.get(base_key, 0) + 1

        updated: List[ScoreColumnSchema] = []
        for schema in schemas:
            base_name = schema.display_name.strip() or f"Cột {schema.leaf_index}"
            name_counts[base_name] = name_counts.get(base_name, 0) + 1
            if name_totals.get(base_name, 0) > 1:
                schema.display_name = f"{base_name} ({name_counts[base_name]})"
            else:
                schema.display_name = base_name

            base_key = schema.column_key.strip() or f"col_{schema.leaf_index}"
            key_counts[base_key] = key_counts.get(base_key, 0) + 1
            if key_totals.get(base_key, 0) > 1:
                schema.column_key = f"{base_key}_{key_counts[base_key]}"
            else:
                schema.column_key = base_key
            updated.append(schema)
        return updated

    def _scorebook_leaf_count(self, body_rows: List[Dict[str, object]]) -> int:
        """Returns the maximum number of visible leaf cells across sampled score rows."""
        return max((len(list(row.get("cells", []))) for row in body_rows), default=0)

    def _build_scorebook_header_grids(
        self,
        header_rows: List[Dict[str, object]],
        leaf_count: int,
    ) -> Tuple[List[List[str]], List[List[Dict[str, object] | None]]]:
        """Expands scorebook header rowspans/colspans into one leaf-aligned grid."""
        header_grid: List[List[str]] = [["" for _ in range(leaf_count)] for _ in range(len(header_rows))]
        header_meta_grid: List[List[Dict[str, object] | None]] = [
            [None for _ in range(leaf_count)] for _ in range(len(header_rows))
        ]

        for row_index, row in enumerate(header_rows):
            col_pos = 0
            for cell in list(row.get("cells", [])):
                while col_pos < leaf_count and header_meta_grid[row_index][col_pos] is not None:
                    col_pos += 1
                colspan = max(1, int(cell.get("colspan", 1) or 1))
                rowspan = max(1, int(cell.get("rowspan", 1) or 1))
                label = (
                    str(cell.get("text", "")).strip()
                    or str(cell.get("cn", "")).strip()
                    or str(cell.get("cl", "")).strip()
                )
                for row_offset in range(rowspan):
                    target_row = row_index + row_offset
                    if target_row >= len(header_rows):
                        break
                    for col_offset in range(colspan):
                        target_col = col_pos + col_offset
                        if target_col >= leaf_count:
                            break
                        header_grid[target_row][target_col] = label
                        header_meta_grid[target_row][target_col] = dict(cell)
                col_pos += colspan

        return header_grid, header_meta_grid

    def _sample_scorebook_leaf_cells(
        self,
        body_rows: List[Dict[str, object]],
        leaf_index: int,
    ) -> List[Dict[str, object]]:
        """Returns the sampled body cells for one leaf column."""
        return [
            row["cells"][leaf_index]
            for row in body_rows
            if leaf_index < len(list(row.get("cells", [])))
        ]

    def _deduped_scorebook_header_path(
        self,
        header_grid: List[List[str]],
        leaf_index: int,
    ) -> Tuple[str, ...]:
        """Builds the de-duplicated header label path for one leaf column."""
        header_path = tuple(
            label
            for label in (
                header_grid[row_index][leaf_index].strip()
                for row_index in range(len(header_grid))
            )
            if label
        )
        deduped_header_path: List[str] = []
        for label in header_path:
            if not deduped_header_path or deduped_header_path[-1] != label:
                deduped_header_path.append(label)
        return tuple(deduped_header_path)

    def _deepest_scorebook_header_meta(
        self,
        header_meta_grid: List[List[Dict[str, object] | None]],
        leaf_index: int,
    ) -> Dict[str, object]:
        """Returns the deepest non-empty header metadata cell for one leaf column."""
        return next(
            (
                header_meta_grid[row_index][leaf_index]
                for row_index in range(len(header_meta_grid) - 1, -1, -1)
                if header_meta_grid[row_index][leaf_index] is not None
            ),
            None,
        ) or {}

    def _infer_scorebook_column_identity(
        self,
        *,
        input_class: str,
        td_class: str,
        normalized_header: str,
        data_column: str,
        cn_value: str,
        block_index: str,
        child_index: str,
        header_label: str,
        leaf_index: int,
    ) -> Tuple[str, str, str]:
        """Infers the semantic role, input kind, and stable key for one scorebook column."""
        role_hint = "static"
        input_kind = ""
        column_key = f"col_{leaf_index}"

        if "input_nhan_xet" in input_class:
            return "comment", "comment", "comment"
        if "input_diem_tbm" in input_class or "tbhk_tt" in td_class.lower():
            return "average", "score", "average_term"
        if "input_diem" in input_class or "txtdiem" in input_class.lower():
            score_suffix = data_column or cn_value or f"{block_index}_{child_index}"
            return "score", "score", f"score_{score_suffix}".strip("_")
        if any(
            token in normalized_header
            for token in (
                "đtbmhk",
                "tbhk",
                "tbhk 1",
                "tbhk 2",
                "tb cả năm",
                "tb ca nam",
                "trung bình học kỳ",
                "trung binh hoc ky",
                "trung bình cả năm",
                "trung binh ca nam",
            )
        ):
            static_average_key = data_column or cn_value or header_label or f"average_{leaf_index}"
            return (
                "average",
                "score",
                f"average_{re.sub(r'[^a-z0-9]+', '_', static_average_key.lower()).strip('_') or leaf_index}",
            )
        if any(
            token in normalized_header
            for token in (
                "điểm thi lại",
                "diem thi lai",
                "thi lại",
                "thi lai",
            )
        ):
            static_score_key = data_column or cn_value or header_label or f"score_{leaf_index}"
            return (
                "score",
                "score",
                f"score_{re.sub(r'[^a-z0-9]+', '_', static_score_key.lower()).strip('_') or leaf_index}",
            )
        if "mã hs" in normalized_header:
            return "student_code", input_kind, "student_code"
        if "họ và tên" in normalized_header:
            return "student_name", input_kind, "student_name"
        if "ngày sinh" in normalized_header:
            return "birth_date", input_kind, "birth_date"
        if "liên lạc" in normalized_header:
            return "contact", input_kind, "contact"
        if "stt" in normalized_header:
            return "ordinal", input_kind, "ordinal"
        return role_hint, input_kind, column_key

    def _scorebook_column_display_name(
        self,
        role_hint: str,
        data_column: str,
        header_label: str,
        cn_value: str,
    ) -> str:
        """Resolves the UI display name for one inferred scorebook schema column."""
        if role_hint == "score" and cn_value and data_column:
            return f"{cn_value} / {data_column}"
        if role_hint == "score" and cn_value:
            return cn_value
        return data_column or header_label

    def _build_scorebook_schema_for_leaf(
        self,
        body_rows: List[Dict[str, object]],
        header_grid: List[List[str]],
        header_meta_grid: List[List[Dict[str, object] | None]],
        leaf_index: int,
    ) -> ScoreColumnSchema:
        """Builds one typed schema object for a single leaf column."""
        sample_cells = self._sample_scorebook_leaf_cells(body_rows, leaf_index)
        first_nonempty_cell = next(
            (
                cell
                for cell in sample_cells
                if str(cell.get("text", "")).strip() or cell.get("inputInfo")
            ),
            sample_cells[0] if sample_cells else {},
        )
        input_info = next((cell.get("inputInfo") for cell in sample_cells if cell.get("inputInfo")), None) or {}
        header_path = self._deduped_scorebook_header_path(header_grid, leaf_index)
        deepest_meta = self._deepest_scorebook_header_meta(header_meta_grid, leaf_index)

        td_class = str(first_nonempty_cell.get("className", "")).strip()
        input_class = str(input_info.get("className", "")).strip()
        header_label = header_path[-1] if header_path else f"Cột {leaf_index}"
        sample_value = str(input_info.get("value", "")).strip() or str(first_nonempty_cell.get("text", "")).strip()
        block_index = str(input_info.get("b", "")).strip() or str(deepest_meta.get("b", "")).strip()
        child_index = str(input_info.get("c", "")).strip() or str(deepest_meta.get("c", "")).strip()
        data_column = str(first_nonempty_cell.get("dataCot", "")).strip() or str(deepest_meta.get("cl", "")).strip()
        cn_value = str(deepest_meta.get("cn", "")).strip()
        normalized_header = " ".join(header_path).lower()
        editable = any(bool(cell.get("inputInfo")) for cell in sample_cells)
        role_hint, input_kind, column_key = self._infer_scorebook_column_identity(
            input_class=input_class,
            td_class=td_class,
            normalized_header=normalized_header,
            data_column=data_column,
            cn_value=cn_value,
            block_index=block_index,
            child_index=child_index,
            header_label=header_label,
            leaf_index=leaf_index,
        )
        display_name = self._scorebook_column_display_name(
            role_hint,
            data_column=data_column,
            header_label=header_label,
            cn_value=cn_value,
        )

        return ScoreColumnSchema(
            column_key=column_key,
            header_path=header_path,
            leaf_index=leaf_index,
            display_name=display_name,
            editable=editable,
            role_hint=role_hint,
            input_kind=input_kind,
            sample_value=sample_value,
            block_index=block_index,
            child_index=child_index,
            data_column=data_column,
            input_name=str(input_info.get("name", "")).strip(),
        )

    def _extract_scorebook_schema_from_snapshot(self, snapshot: Dict[str, object]) -> List[ScoreColumnSchema]:
        """Builds leaf-column schema objects from one raw table snapshot."""
        header_rows = list(snapshot.get("scoreTableHeaderRows", []))
        body_rows = list(snapshot.get("scoreTableBodyRows", []))
        if not header_rows or not body_rows:
            return []

        leaf_count = self._scorebook_leaf_count(body_rows)
        if leaf_count <= 0:
            return []

        header_grid, header_meta_grid = self._build_scorebook_header_grids(header_rows, leaf_count)
        schemas = [
            self._build_scorebook_schema_for_leaf(
                body_rows,
                header_grid,
                header_meta_grid,
                leaf_index,
            )
            for leaf_index in range(leaf_count)
        ]

        return self._finalize_schema_identity(schemas)

    def _find_schema_by_key(
        self, schemas: List[ScoreColumnSchema], column_key: str
    ) -> ScoreColumnSchema | None:
        """Finds one parsed schema by its stable key."""
        column_key = column_key.strip()
        if not column_key:
            return None
        return next((schema for schema in schemas if schema.column_key == column_key), None)

    def _detect_scorebook_columns(self, schemas: List[ScoreColumnSchema]) -> ScorebookDetectedColumns:
        """Chooses comment/score columns from the parsed schema using deterministic heuristics."""
        editable_comment_columns = [
            schema for schema in schemas if schema.editable and schema.role_hint == "comment"
        ]
        average_columns = [
            schema for schema in schemas if schema.role_hint == "average"
        ]
        score_columns = [
            schema for schema in schemas if schema.role_hint == "score"
        ]
        all_score_candidates = sorted(
            [*average_columns, *score_columns],
            key=lambda schema: schema.leaf_index,
        )

        preferred_score_column_key = ""
        preferred_score_reason = ""

        average_with_data = [schema for schema in average_columns if schema.sample_value.strip()]
        score_with_data = [schema for schema in score_columns if schema.sample_value.strip()]
        if average_with_data:
            preferred_schema = max(average_with_data, key=lambda schema: schema.leaf_index)
            preferred_score_column_key = preferred_schema.column_key
            preferred_score_reason = "Ưu tiên cột trung bình có dữ liệu."
        elif score_with_data:
            preferred_schema = max(score_with_data, key=lambda schema: schema.leaf_index)
            preferred_score_column_key = preferred_schema.column_key
            preferred_score_reason = "Ưu tiên cột điểm ngoài cùng bên phải hiện đã có dữ liệu."
        elif average_columns:
            preferred_schema = max(average_columns, key=lambda schema: schema.leaf_index)
            preferred_score_column_key = preferred_schema.column_key
            preferred_score_reason = "Fallback sang cột trung bình ngoài cùng bên phải."
        elif score_columns:
            preferred_schema = max(score_columns, key=lambda schema: schema.leaf_index)
            preferred_score_column_key = preferred_schema.column_key
            preferred_score_reason = "Fallback sang cột điểm ngoài cùng bên phải."

        preferred_comment_column_key = ""
        if editable_comment_columns:
            preferred_comment_column_key = min(
                editable_comment_columns,
                key=lambda schema: schema.leaf_index,
            ).column_key

        return ScorebookDetectedColumns(
            preferred_score_column_key=preferred_score_column_key,
            preferred_score_reason=preferred_score_reason,
            preferred_comment_column_key=preferred_comment_column_key,
            score_candidate_keys=[schema.column_key for schema in all_score_candidates],
            average_candidate_keys=[schema.column_key for schema in average_columns],
            comment_candidate_keys=[schema.column_key for schema in editable_comment_columns],
        )
