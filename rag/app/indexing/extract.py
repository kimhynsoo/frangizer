"""원본 파일에서 쪽 단위 본문을 뽑는다.

- page는 PDF 파일 기준 1부터 센다. 제외한 쪽이 있어도 다시 세지 않는다.
- 여러 쪽에 반복되는 머리말·꼬리말(법제처 머리글, '페이지 N / M' 등)만 지운다.
  본문 문장·수치·조건은 손대지 않는다.
- 글자가 거의 없는 쪽은 스캔본일 수 있어 OCR 검토 대상으로 보고한다(자동 OCR 하지 않음).
- 표는 PyMuPDF로 위치만 찾아 보고한다. 표 셀 추출은 칸이 깨지는 경우가 있어 본문으로 쓰지 않는다.
"""
import re
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

SCAN_MIN_CHARS = 30
EDGE_LINES = 3  # 머리말·꼬리말 후보로 보는 쪽 위·아래 줄 수
REPEAT_RATIO = 0.6


class ExtractionError(Exception):
    pass


@dataclass
class PageText:
    page: int | None  # PDF 1부터. Markdown/TXT는 None
    lines: list[str]
    removed_edge_lines: int = 0
    table_count: int = 0
    flags: list[str] = field(default_factory=list)

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


def extract_pdf(path, pages=None):
    """pages: 처리할 PDF 쪽 번호 집합(1부터). None이면 전체."""
    try:
        doc = pymupdf.open(path)
    except Exception as e:  # 손상·암호화 파일
        raise ExtractionError(f"PDF를 열 수 없음: {e}") from e
    out = []
    for i, pg in enumerate(doc):
        no = i + 1
        if pages is not None and no not in pages:
            continue
        raw = pg.get_text()
        lines = [l.replace("\xa0", " ").strip() for l in raw.splitlines()]
        lines = [l for l in lines if l]
        p = PageText(page=no, lines=lines)
        if len("".join(lines)) < SCAN_MIN_CHARS:
            p.flags.append("low_text_possible_scan")
        try:
            p.table_count = len(pg.find_tables().tables)
        except Exception:
            p.flags.append("table_detection_failed")
        if p.table_count:
            p.flags.append("table_needs_review")
        out.append(p)
    _strip_repeated_edges(out)
    return out, doc.page_count


def extract_text(path):
    """Markdown/TXT: 한 덩어리로 읽고 page=None. 제목 구조는 분할 단계에서 쓴다."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ExtractionError("UTF-8 텍스트가 아님") from e
    return [PageText(page=None, lines=[l.rstrip() for l in text.splitlines()])], None
