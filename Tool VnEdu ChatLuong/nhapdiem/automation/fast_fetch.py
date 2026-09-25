"""Lấy nhanh dữ liệu điểm qua API VNEDU (bỏ qua combo ExtJS)."""

from __future__ import annotations

from ..scorebook_core import VnEduScoreAutomation


# ---------------------------------------------------------------------------
#  FAST DIRECT FETCH: bypass ExtJS combo cascade, call VNEDU API directly
# ---------------------------------------------------------------------------

_FAST_SCORE_FETCH_JS = """async ({ params, targetColumnKey, windowId }) => {
    const resp = await fetch('/v5/?load=edu.so_diem.nhap', {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
        body: new URLSearchParams(params).toString(),
    });
    const html = await resp.text();
    if (!html || html.length < 100) return { error: 'empty_response' };

    // Inject into live page so future writes work correctly
    const panel = document.getElementById(windowId + '_panel_content');
    if (panel) panel.innerHTML = html;
    const hKhoi = document.getElementById('iKhoi');
    if (hKhoi) hKhoi.value = params.iKhoi || '';
    const hLop = document.getElementById('iLopId');
    if (hLop) hLop.value = params.iLopId || '';
    const hMon = document.getElementById('iMonHocId');
    if (hMon) hMon.value = params.iMonHocId || '';
    const hHK = document.getElementById('iHocKy');
    if (hHK) hHK.value = params.iHocKyId || '';

    // Parse the HTML with DOMParser
    const doc = new DOMParser().parseFromString(html, 'text/html');

    const permCell = Array.from(doc.querySelectorAll('td')).find(td =>
        /quyền hạn/i.test((td.textContent || '').trim()));
    const permissionText = (permCell?.textContent || '').trim();
    const teacherText = (doc.querySelector('#gvbm')?.textContent || '').trim();

    const commentInputs = Array.from(doc.querySelectorAll('input.input_nhan_xet'));
    const enabledCommentCount = commentInputs.filter(
        inp => !inp.disabled && !inp.readOnly).length;

    const table = doc.querySelector('table.tablefix');
    const numHdrRows = 2;

    // --- Enhanced column schema extraction with role inference ---
    // H2 FIX: build the schema FIRST so the target input can be located by its
    // leaf-column index (mirrors the slow path's target_schema.leaf_index),
    // instead of fragile b/c parsing from the column key string. This makes
    // average/static/score columns all resolvable.
    const schemas = [];
    let targetLeafIndex = -1;
    if (table && table.rows.length > 2) {
        const firstDataRow = table.rows[numHdrRows];
        const leafCount = firstDataRow ? firstDataRow.cells.length : 0;

        // Build leaf-aligned header grid (expand colspan/rowspan)
        const hdrGrid = Array.from({length: numHdrRows}, () => new Array(leafCount).fill(''));
        const hdrMeta = Array.from({length: numHdrRows}, () => new Array(leafCount).fill(null));
        for (let r = 0; r < numHdrRows && r < table.rows.length; r++) {
            let col = 0;
            for (let c = 0; c < table.rows[r].cells.length; c++) {
                const cell = table.rows[r].cells[c];
                const text = (cell.textContent || '').trim();
                const cs = cell.colSpan || 1;
                const rs = cell.rowSpan || 1;
                while (col < leafCount && hdrMeta[r][col] !== null) col++;
                for (let dr = 0; dr < rs && (r + dr) < numHdrRows; dr++) {
                    for (let dc = 0; dc < cs; dc++) {
                        const tc = col + dc;
                        if (tc < leafCount && hdrMeta[r + dr][tc] === null) {
                            hdrGrid[r + dr][tc] = text;
                            hdrMeta[r + dr][tc] = {
                                cn: cell.getAttribute('cn') || '',
                                cl: cell.getAttribute('cl') || '',
                            };
                        }
                    }
                }
                col += cs;
            }
        }

        for (let li = 0; li < leafCount; li++) {
            // M2 FIX: scan up to 5 data rows (mirrors slow path sample_cells) so a
            // readonly/transfer first row does not mislabel an editable column.
            const sampleRowLimit = Math.min(table.rows.length, numHdrRows + 5);
            let inp = null;
            let editable = false;
            let sampleValue = '';
            for (let sr = numHdrRows; sr < sampleRowLimit; sr++) {
                const sampleCell = table.rows[sr].cells[li];
                if (!sampleCell) continue;
                const candidate = sampleCell.querySelector('input, textarea');
                if (candidate) {
                    if (inp === null) inp = candidate;
                    if (!candidate.readOnly && !candidate.disabled) editable = true;
                    if (!sampleValue && (candidate.value || '').trim()) sampleValue = (candidate.value || '').trim();
                } else if (!sampleValue) {
                    const cellText = (sampleCell.textContent || '').trim();
                    if (cellText) sampleValue = cellText;
                }
            }
            const td = firstDataRow.cells[li];
            const inputClass = inp ? (inp.className || '') : '';
            const tdClass = (inp && inp.closest ? (inp.closest('td')?.className || '') : (td ? td.className || '' : ''));
            const dataColumn = (inp && inp.closest ? (inp.closest('td')?.getAttribute('data-cot') || '') : (td ? td.getAttribute('data-cot') || '' : ''));
            const blockIndex = inp ? (inp.getAttribute('b') || '') : '';
            const childIndex = inp ? (inp.getAttribute('c') || '') : '';
            const inputName = inp ? (inp.name || '') : '';

            // De-duplicated header path
            const rawPath = [];
            for (let r = 0; r < numHdrRows; r++) {
                const lbl = (hdrGrid[r][li] || '').trim();
                if (lbl && (rawPath.length === 0 || rawPath[rawPath.length - 1] !== lbl))
                    rawPath.push(lbl);
            }
            // Deepest header cn value
            let cnValue = '';
            for (let r = numHdrRows - 1; r >= 0; r--) {
                const m = hdrMeta[r][li];
                if (m && m.cn) { cnValue = m.cn; break; }
            }

            const headerLabel = rawPath.length > 0 ? rawPath[rawPath.length - 1] : ('Col ' + li);
            const normHdr = rawPath.join(' ').toLowerCase();

            // --- Column identity inference (mirrors Python _infer_scorebook_column_identity) ---
            let roleHint = 'static', inputKind = '', columnKey = 'col_' + li;
            const icLow = inputClass.toLowerCase();
            const tdLow = tdClass.toLowerCase();
            if (icLow.includes('input_nhan_xet')) {
                roleHint = 'comment'; inputKind = 'comment'; columnKey = 'comment';
            } else if (icLow.includes('input_diem_tbm') || tdLow.includes('tbhk_tt')) {
                roleHint = 'average'; inputKind = 'score'; columnKey = 'average_term';
            } else if (icLow.includes('input_diem') || icLow.includes('txtdiem')) {
                const sfx = dataColumn || cnValue || (blockIndex + '_' + childIndex);
                roleHint = 'score'; inputKind = 'score';
                columnKey = 'score_' + sfx.replace(/_+$/g, '');
            } else {
                const avgTk = ['\\u0111tbmhk','tbhk','tbhk 1','tbhk 2',
                    'tb c\\u1ea3 n\\u0103m','tb ca nam',
                    'trung b\\u00ecnh h\\u1ecdc k\\u1ef3','trung binh hoc ky',
                    'trung b\\u00ecnh c\\u1ea3 n\\u0103m','trung binh ca nam'];
                const scTk = ['\\u0111i\\u1ec3m thi l\\u1ea1i','diem thi lai',
                    'thi l\\u1ea1i','thi lai'];
                if (avgTk.some(t => normHdr.includes(t))) {
                    const k = (dataColumn || cnValue || headerLabel || ('average_' + li))
                        .toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
                    roleHint = 'average'; inputKind = 'score';
                    columnKey = 'average_' + (k || li);
                } else if (scTk.some(t => normHdr.includes(t))) {
                    const k = (dataColumn || cnValue || headerLabel || ('score_' + li))
                        .toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
                    roleHint = 'score'; inputKind = 'score';
                    columnKey = 'score_' + (k || li);
                } else if (normHdr.includes('m\\u00e3 hs') || normHdr.includes('ma hs')) {
                    roleHint = 'student_code'; columnKey = 'student_code';
                } else if (normHdr.includes('h\\u1ecd v\\u00e0 t\\u00ean')
                        || normHdr.includes('ho va ten')) {
                    roleHint = 'student_name'; columnKey = 'student_name';
                } else if (normHdr.includes('ng\\u00e0y sinh') || normHdr.includes('ngay sinh')) {
                    roleHint = 'birth_date'; columnKey = 'birth_date';
                } else if (normHdr.includes('li\\u00ean l\\u1ea1c') || normHdr.includes('lien lac')) {
                    roleHint = 'contact'; columnKey = 'contact';
                } else if (normHdr.includes('stt')) {
                    roleHint = 'ordinal'; columnKey = 'ordinal';
                }
            }

            // Display name inference (mirrors Python _scorebook_column_display_name)
            let displayName = dataColumn || headerLabel;
            if (roleHint === 'score' && cnValue && dataColumn)
                displayName = cnValue + ' / ' + dataColumn;
            else if (roleHint === 'score' && cnValue)
                displayName = cnValue;

            // H2 FIX: remember the leaf index that matches the requested column key.
            if (targetLeafIndex < 0 && columnKey === String(targetColumnKey || '')) {
                targetLeafIndex = li;
            }

            schemas.push({
                column_key: columnKey,
                display_name: displayName,
                header_path: rawPath,
                leaf_index: li,
                editable: editable,
                role_hint: roleHint,
                input_kind: inputKind,
                sample_value: sampleValue,
                block_index: blockIndex,
                child_index: childIndex,
                data_column: dataColumn,
                input_name: inputName,
            });
        }
    }

    // H2 FIX: Extract per-student rows using leaf-column indices derived from the
    // schema (not b/c parsed from the key string). This resolves the target input
    // for score / average / static columns alike, mirroring the slow path.
    let codeLeafIndex = -1;
    let nameLeafIndices = [];
    for (const s of schemas) {
        if (s.role_hint === 'student_code' && codeLeafIndex < 0) codeLeafIndex = s.leaf_index;
        if (s.role_hint === 'student_name') nameLeafIndices.push(s.leaf_index);
    }
    if (codeLeafIndex < 0) codeLeafIndex = 1;

    const entries = [];
    if (table && targetLeafIndex >= 0) {
        for (let r = numHdrRows; r < table.rows.length; r++) {
            const row = table.rows[r];
            if (row.cells.length <= targetLeafIndex) continue;
            const codeCell = row.cells[codeLeafIndex];
            const studentCode = codeCell ? (codeCell.textContent || '').trim() : '';
            let fullName = '';
            if (nameLeafIndices.length > 0) {
                fullName = nameLeafIndices
                    .map(idx => (row.cells[idx]?.textContent || '').trim())
                    .filter(Boolean)
                    .join(' ')
                    .trim();
            }
            if (!fullName) {
                // Fallback: first non-numeric, non-date cell among the first 6 columns.
                for (let ci = 0; ci < Math.min(6, row.cells.length); ci++) {
                    const t = (row.cells[ci]?.textContent || '').trim();
                    if (t && !/^\\d+$/.test(t) && !/\\d{2}\\/\\d{2}\\/\\d{4}/.test(t)) { fullName = t; break; }
                }
            }
            const targetCell = row.cells[targetLeafIndex];
            const targetInput = targetCell
                ? targetCell.querySelector('input, textarea, select, span.input_diem, span.input_nhan_xet')
                : null;
            let targetInputName = '';
            let currentScore = '';
            if (targetInput) {
                targetInputName = targetInput.name || targetInput.id || '';
                const tag = String(targetInput.tagName || '').toUpperCase();
                currentScore = tag === 'SPAN'
                    ? (targetInput.textContent || targetInput.innerText || '').trim()
                    : (targetInput.value || '').trim();
            }
            let studentId = targetInput ? (targetInput.getAttribute && targetInput.getAttribute('a') || '') : '';
            if (!studentId) {
                const anyInput = row.querySelector('input[a]');
                if (anyInput) studentId = anyInput.getAttribute('a') || '';
            }
            // Skip fully empty rows (no code, no name, no input).
            if (!studentCode && !fullName && !targetInputName) continue;
            entries.push({
                row_index: r - numHdrRows,
                row_id: studentId || studentCode,
                student_code: studentCode,
                student_name: fullName,
                current_score: currentScore,
                target_input_name: targetInputName,
            });
        }
    }

    return {
        entries,
        permissionText,
        teacherText,
        commentInputCount: commentInputs.length,
        enabledCommentCount,
        schemas,
        targetLeafIndex,
    };
}"""


def _direct_fetch_score_entries(
    automation: VnEduScoreAutomation,
    page: object,
    snapshot: dict[str, object],
    *,
    grade_id: str,
    class_id: str,
    subject_id: str,
    term_id: str,
    target_column_key: str,
) -> dict[str, object] | None:
    """Fetches the score table via a single direct POST, skipping all ExtJS combo cascades.

    Returns a dict with 'entries', 'permissionText', 'teacherText', 'schemas', etc.
    Returns None if the fast path cannot be used.
    """
    school_year = (
        str(snapshot.get("hiddenSchoolYear", "")).strip()
        or str(snapshot.get("currentSchoolYear", "")).strip()
    )
    window_id = str(snapshot.get("windowId", "")).strip()
    if not school_year or not window_id:
        return None

    params = {
        "app_nam_hoc": school_year,
        "nam_hoc": school_year,
        "winid": window_id,
        "iLopId": str(class_id),
        "iKhoi": str(grade_id),
        "iMonHocId": str(subject_id),
        "iHocKyId": str(term_id),
        "deleteMode": "0",
        "thang": "0",
        "muc": "",
        "dot_diem_id": "0",
        "dot_diem_data": "",
        "dot_id": "0",
        "dm_cua_mon_hoc": "0",
        "dm_cua_khoi_hoc": "0",
        "dm_cua_toi": "0",
    }
    result = page.evaluate(
        _FAST_SCORE_FETCH_JS,
        {
            "params": params,
            "targetColumnKey": target_column_key,
            "windowId": window_id,
        },
    )
    if not isinstance(result, dict) or result.get("error"):
        return None
    return result
