"""원본 파일에서 쪽 단위 본문을 뽑는다.

- page는 PDF 파일 기준 1부터 센다. 제외한 쪽이 있어도 다시 세지 않는다.
- 여러 쪽에 반복되는 머리말·꼬리말(법제처 머리글, '페이지 N / M' 등)만 지운다.
  본문 문장·수치·조건은 손대지 않는다.
- 글자가 거의 없는 쪽은 스캔본일 수 있어 OCR 검토 대상으로 보고한다(자동 OCR 하지 않음).
- 표는 영역을 제한한 Camelot/pdfplumber로 후보 셀을 추출한다. 미검수 후보는 답변 근거로 쓰지 않는다.
"""
import re
import hashlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

SCAN_MIN_CHARS = 30
EDGE_LINES = 3  # 머리말·꼬리말 후보로 보는 쪽 위·아래 줄 수
REPEAT_RATIO = 0.6


class ExtractionError(Exception):
    pass


@dataclass
class TextRegion:
    kind: str  # prose | table. 경계를 모르면 분리하지 않고 기존 쪽 경고를 유지한다.
    lines: list[str]
    table: dict | None = None


@dataclass
class PageText:
    page: int | None  # PDF 1부터. Markdown/TXT는 None
    lines: list[str]
    removed_edge_lines: int = 0
    table_count: int = 0
    flags: list[str] = field(default_factory=list)
    regions: list[TextRegion] = field(default_factory=list)

    @property
    def text(self):
        return "\n".join(self.lines)


def _norm(line):
    return re.sub(r"\d+", "#", line.replace("\xa0", " ")).strip()


def _strip_repeated_edges(pages):
    """쪽 위·아래 EDGE_LINES 줄 중 전체 쪽의 REPEAT_RATIO 이상에서 반복되는 줄을 지운다."""
    if len(pages) < 3:
        return
    counts = Counter()
    for p in pages:
        edge = p.lines[:EDGE_LINES] + p.lines[-EDGE_LINES:]
        counts.update({_norm(l) for l in edge})
    repeated = {k for k, n in counts.items() if n >= REPEAT_RATIO * len(pages) and k}
    for p in pages:
        keep = []
        last = len(p.lines) - EDGE_LINES
        for i, line in enumerate(p.lines):
            if (i < EDGE_LINES or i >= last) and _norm(line) in repeated:
                p.removed_edge_lines += 1
                continue
            keep.append(line)
        p.lines = keep


DISPUTE_HEADERS = ("분쟁유형", "해결기준", "비고")
PAIR_MARK = "⇒ "
NOTE_MARK = "[비고]"


def _layout_lines(pg):
    out = []
    for b in pg.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            t = "".join(s["text"] for s in l["spans"]).replace("\xa0", " ").strip()
            if t:
                out.append((l["bbox"][0], l["bbox"][1], t, l["bbox"][2]))
    return out


def pair_dispute_rows(pg):
    """소비자분쟁해결기준 별표의 `분쟁유형 | 해결기준 | 비고` 표를 행 단위로 다시 쓴다.

    일반 텍스트 추출은 칸 단위(분쟁유형 전부 → 해결기준 전부)로 나와 '어떤 유형에 어떤 기준'인지가 순서로만 남는다.
    같은 높이(또는 바로 위)의 분쟁유형 줄 밑에 해결기준 줄을 `⇒`로 붙이고, 비고는 [비고] 아래에 모은다.
    원문 줄은 그대로 두고 표시만 더한다. 머리글이 없으면 None.
    """
    lines = _layout_lines(pg)
    heads = {re.sub(r"\s+", "", t): ((x + x1) / 2, y) for x, y, t, x1 in lines if re.sub(r"\s+", "", t) in DISPUTE_HEADERS}
    if len(heads) != 3:
        return None
    head_y = max(y for _, y in heads.values())
    # 머리글은 칸 가운데 정렬이라 머리글 중심점 사이의 중간을 칸 경계로 쓴다
    c0, c1, c2 = (heads[h][0] for h in DISPUTE_HEADERS)
    b1, b2 = (c0 + c1) / 2, (c1 + c2) / 2
    lines = [(x, y, t) for x, y, t, _ in lines]
    above = [t for x, y, t in sorted(lines, key=lambda r: (r[1], r[0])) if y < head_y - 2]
    body = [(x, y, t) for x, y, t in lines if y >= head_y - 2 and re.sub(r"\s+", "", t) not in DISPUTE_HEADERS]
    col0 = sorted([(y, t) for x, y, t in body if x < b1])
    col1 = sorted([(y, t) for x, y, t in body if b1 <= x < b2])
    col2 = sorted([(y, t) for x, y, t in body if x >= b2])
    attached = {i: [] for i in range(len(col0))}
    lead = []  # 첫 분쟁유형보다 위에 있는 해결기준 줄
    for y, t in col1:
        anchors = [i for i, (y0, _) in enumerate(col0) if y0 <= y + 2]
        (attached[anchors[-1]] if anchors else lead).append(t)
    out = above + list(DISPUTE_HEADERS) + [PAIR_MARK + t for t in lead]  # 머리글은 공백만 정리해 남긴다
    for i, (_, t) in enumerate(col0):
        out.append(t)
        out.extend(PAIR_MARK + a for a in attached[i])
    if col2:
        out.append(NOTE_MARK)
        out.extend(t for _, t in col2)
    return out


def _same_chars(a, b):
    strip = lambda s: Counter(re.sub(r"\s+", "", s))
    return strip(a) == strip(b)


def separate_table_regions(pg, lines, boxes):
    """검출된 표 안의 글과 밖의 설명을 분리한다. 셀 구조의 정확성을 승인하지 않는다.

    경계에 걸친 줄·겹친 표·원문 누락이 있으면 포기해 쪽 전체의 검수 경고를 유지한다.
    표 본문은 원래 읽기 순서를 그대로 보존하고, 구조가 확인되지 않았다는 표시도 유지한다.
    """
    if not boxes:
        return []
    remaining = Counter(lines)
    groups = [[] for _ in range(len(boxes) + 1)]
    for x0, y0, text, x1 in _layout_lines(pg):
        if not remaining[text]:
            continue  # 반복 머리말·꼬리말로 제거된 줄
        remaining[text] -= 1
        owners = []
        for i, (left, top, right, bottom) in enumerate(boxes, 1):
            if top - 2 <= y0 <= bottom and x1 > left and x0 < right:
                if x0 < left - 2 or x1 > right + 2:
                    return []
                owners.append(i)
        if len(owners) > 1:
            return []
        groups[owners[0] if owners else 0].append(text)
    if any(not g for g in groups[1:]) or any(remaining.values()) or not _same_chars('\n'.join(lines), '\n'.join(t for g in groups for t in g)):
        return []
    return [TextRegion('prose' if i == 0 else 'table', group) for i, group in enumerate(groups) if group]


def extract_pdf(path, pages=None, *, parse_tables=True, skip_table_pages=()):
    """pages: 처리할 PDF 쪽 번호 집합(1부터). None이면 전체."""
    try:
        doc = pymupdf.open(path)
    except Exception as e:  # 손상·암호화 파일
        raise ExtractionError(f"PDF를 열 수 없음: {e}") from e
    out, table_boxes = [], {}
    for i, pg in enumerate(doc):
        no = i + 1
        if pages is not None and no not in pages:
            continue
        raw = pg.get_text()
        lines = [l.replace("\xa0", " ").strip() for l in raw.splitlines()]
        lines = [l for l in lines if l]
        paired = pair_dispute_rows(pg)
        mismatch = False
        if paired is not None:
            restored = "\n".join(l for l in paired if l != NOTE_MARK).replace(PAIR_MARK, "")
            if _same_chars(restored, raw):  # 글자 하나라도 빠지거나 늘면 원래 추출을 쓴다
                lines = paired
            else:
                paired, mismatch = None, True
        p = PageText(page=no, lines=lines)
        if len("".join(lines)) < SCAN_MIN_CHARS:
            p.flags.append("low_text_possible_scan")
        try:
            tables = pg.find_tables().tables
            p.table_count = len(tables)
            table_boxes[no] = [tuple(t.bbox) for t in tables]
        except Exception:
            p.flags.append("table_detection_failed")
        if paired is not None:
            p.flags.append("table_rows_paired")
        elif p.table_count:
            p.flags.append("table_needs_review")
        if mismatch:
            p.flags.append("table_pairing_mismatch")
        out.append(p)
    _strip_repeated_edges(out)
    source_sha = None
    for p in out:
        if parse_tables and 'table_needs_review' in p.flags:
            p.regions = separate_table_regions(doc[p.page - 1], p.lines, table_boxes.get(p.page, []))
            if p.regions and p.page not in skip_table_pages:
                from .tables import extract_local_tables, table_lines
                source_sha = source_sha or hashlib.sha256(Path(path).read_bytes()).hexdigest()
                table_regions = [r for r in p.regions if r.kind == 'table']
                context = [line for r in p.regions if r.kind == 'prose' for line in r.lines]
                candidates = extract_local_tables(path, p.page, table_boxes[p.page], doc[p.page-1].rect.height,
                    [r.lines for r in table_regions], context, source_sha)
                for region, candidate in zip(table_regions, candidates):
                    if candidate:
                        region.table = candidate
                        region.lines = table_lines(candidate)
    page_count = doc.page_count
    doc.close()
    return out, page_count


def extract_text(path):
    """Markdown/TXT: 한 덩어리로 읽고 page=None. 제목 구조는 분할 단계에서 쓴다."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ExtractionError("UTF-8 텍스트가 아님") from e
    return [PageText(page=None, lines=[l.rstrip() for l in text.splitlines()])], None
