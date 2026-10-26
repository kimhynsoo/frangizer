"""Claude가 PDF 표를 읽어 옮겨 적은 기록(A)을 색인에 반영한다 (QA-03).

기록: data/structured/table_transcriptions.json (rag 컨테이너에는 data/structured가 읽기 전용으로 연결된다)
다른 팀원의 표 후보 승인 기록(table_reviews.json, tables.py·table_review.py)과는 별개 파일·별개 형식이다.
- 원본 파일 sha256에 묶는다. 파일이 바뀌면 기록이 적용되지 않는다.
- pages: 값을 대조할 쪽(열 이름이 앞 쪽에 있으면 그 쪽도), covers: 검수 표시를 바꿀 쪽(기본 pages).
  한 쪽의 표를 모두 검수했을 때만 그 쪽을 covers에 넣는다.
- 표마다 행을 옮겨 적는다. 병합된 칸은 해당 행마다 같은 값을 적는다.
- 모든 칸 값·제목·열 이름은 그 표가 걸친 쪽의 원문 텍스트에 공백만 빼고 그대로 있어야 한다
  (없는 숫자·문구를 넣을 수 없다). 합계 검산(sum_checks)이 있으면 값도 맞아야 한다.
- rows가 없는 항목은 '일반 텍스트가 행 순서대로 추출됨'을 확인한 쪽이다.

적용: 검수한 쪽의 'table_needs_review' 표시를 'table_reviewed'로 바꾸고, 표가 시작하는 쪽 본문 앞에
'[검수된 표 · PDF 원본 대조]' 블록을 붙인다. 원래 추출 텍스트는 그대로 둔다.
검증에 실패한 기록은 적용하지 않고 품질 보고서에 남긴다(색인 작업은 실패시키지 않는다).
파일을 읽을 수 없거나 JSON이 깨져 있어도 색인은 계속하고, 품질 보고서에 TABLE_TRANSCRIPTION_UNREADABLE을 남긴다.
"""
import hashlib
import json
import os
import re
from pathlib import Path

REVIEW_MARK = "[검수된 표 · PDF 원본 대조]"
FILE_NAME = "table_transcriptions.json"
EDGE_RE = re.compile(r"^(㈜더본코리아-빽다방-\d+ 접수본|페이지 \d+ / \d+)$")


def review_file():
    """TABLE_TRANSCRIPTION_FILE > RECIPE_STRUCTURED_FILE과 같은 폴더 > 저장소 data/structured. 없으면 None."""
    if os.environ.get("TABLE_TRANSCRIPTION_FILE"):
        return Path(os.environ["TABLE_TRANSCRIPTION_FILE"])
    if os.environ.get("RECIPE_STRUCTURED_FILE"):
        p = Path(os.environ["RECIPE_STRUCTURED_FILE"]).parent / FILE_NAME
        if p.exists():
            return p
    p = Path(__file__).resolve().parents[3] / "data" / "structured" / FILE_NAME
    return p if p.exists() else None


def load_reviews(sha256, path=None):
    """해당 파일(sha256)의 검수 기록. 없으면 None."""
    path = path or review_file()
    if not path or not Path(path).exists():
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return next((d for d in data.get("documents", []) if d.get("sha256") == sha256), None)
    except (OSError, ValueError, AttributeError, TypeError) as e:
        # 표 기록 하나 때문에 모든 PDF 색인이 멈추지 않게 한다. 표 쪽은 table_needs_review로 남는다.
        return {"unreadable": f"{type(e).__name__}: {e}"[:200], "tables": []}


def _strip(s):
    return re.sub(r"\s+", "", s)


def _cell(v):
    """칸 값 → (표시 문자열, 대조용 원문 조각).
    - 문자열: 그대로
    - 조각 목록: 원문 줄바꿈·쪽 경계에서 끊긴 칸. 공백으로 이어 표시
    - {"text", "src"}: 단어 중간에서 끊긴 칸. text는 src를 이은 것과 공백만 다를 수 있다"""
    if isinstance(v, dict):
        return v["text"], list(v["src"])
    parts = [v] if isinstance(v, str) else list(v)
    return " ".join(parts), parts


def _page_source(raw_pages, pages):
    lines = []
    for p in pages:
        lines += [l.strip() for l in raw_pages[p].splitlines() if not EDGE_RE.match(l.strip())]
    return _strip("".join(lines))


def validate(entry, raw_pages):
    """검수 항목 하나를 원문과 대조. 문제 목록(빈 목록이면 통과)."""
    errors = []
    pages = entry["pages"]
    missing = [p for p in pages if p not in raw_pages]
    if missing:
        return [f"원문에 없는 쪽 {missing}"]
    src = _page_source(raw_pages, pages)
    texts = [entry.get("title"), entry.get("unit")] + list(entry.get("columns") or [])
    for row in entry.get("rows") or []:
        if len(row) != len(entry["columns"]):
            errors.append(f"열 수 불일치: {row}")
        texts += [c for c in row if c]
    for t in texts:
        if not t:
            continue
        if isinstance(t, dict) and _strip(t["text"]) != _strip("".join(t["src"])):
            errors.append(f"표시 값이 원문 조각과 다름(공백 외 글자 추가·삭제): {t['text']!r}")
        for frag in _cell(t)[1]:
            if _strip(frag) not in src:
                errors.append(f"원문에 없는 값: {frag!r} (쪽 {pages})")
    for chk in entry.get("sum_checks") or []:
        col = entry["columns"].index(chk["column"])
        rows = {_cell(r[0])[0]: r for r in entry["rows"]}
        num = lambda s: int(_strip(_cell(s)[0]).replace(",", ""))
        total = sum(num(rows[k][col]) for k in chk["items"])
        if total != num(rows[chk["total"]][col]):
            errors.append(f"합계 불일치 {chk}: {total}")
    return errors


def render(entry):
    """검수 표 → 행 단위 텍스트. 빈 칸은 적지 않는다."""
    if not entry.get("rows"):
        return []
    head = f"{REVIEW_MARK} {_cell(entry['title'])[0]}" + (f" {_cell(entry['unit'])[0]}" if entry.get("unit") else "")
    out = [head]
    cols = [_cell(c)[0] for c in entry["columns"]]
    for row in entry["rows"]:
        label, rest = (_cell(row[0])[0] if row[0] else ""), []
        for name, value in zip(cols[1:], row[1:]):
            if value:
                rest.append(f"{name} {_cell(value)[0]}")
        out.append(f"- {label}: " + " · ".join(rest) if label else "- " + " · ".join(rest))
    return out


def apply_reviews(pages, raw_pages, review):
    """pages(PageText 목록)에 옮겨 적은 표를 반영. 보고서용 요약을 돌려준다.

    - 표시: 기록이 다 덮는 쪽(covers)은 table_needs_review → table_reviewed
    - 본문: 표 후보 영역 분리(extract.separate_table_regions)가 된 쪽은 자동 추출 표 영역을 빼고 이 기록의 표를
      표 영역(reviewed)으로 넣는다. 설명 글 영역은 그대로. 분리가 안 된 쪽은 쪽 본문 앞에 행 블록을 붙인다.
      원문 줄 순서만 확인한 쪽(rows 없음)은 영역 분리를 취소하고 원래 쪽 본문을 쓴다 — 표시를 풀면서
      검수되지 않은 자동 추출 표 텍스트가 근거로 쓰이지 않게 한다.
    """
    from .extract import TextRegion

    if not review:
        return None
    if review.get("unreadable"):
        return {"unreadable": review["unreadable"], "applied": [], "rejected": [], "digest": None}
    by_page = {p.page: p for p in pages}
    applied, rejected, reviewed_pages, blocks, text_order = [], [], set(), {}, set()
    for entry in review["tables"]:
        errors = validate(entry, raw_pages)
        if errors:
            rejected.append({"pages": entry["pages"], "covers": entry.get("covers", entry["pages"]),
                             "title": entry.get("title"), "errors": errors[:5]})
            continue
        covers = entry.get("covers", entry["pages"])
        reviewed_pages.update(covers)
        if not entry.get("rows"):
            text_order.update(covers)   # 원문 줄 순서만 확인한 쪽: 원래 쪽 본문을 써야 한다
        anchor = (covers or entry["pages"])[0]   # covers가 비면 행 블록만 붙이고 표시는 그대로
        block = render(entry)
        if anchor in by_page and block:
            blocks.setdefault(anchor, []).append((entry, block))
        applied.append({"pages": entry["pages"], "title": entry.get("title"), "rows": len(entry.get("rows") or [])})
    # 검증에 실패한 기록이 걸친 쪽은 표시를 바꾸지 않는다
    rejected_pages = {p for r in rejected for p in r["covers"]}
    final = reviewed_pages - rejected_pages
    for no in final:
        p = by_page.get(no)
        if p is not None and "table_needs_review" in p.flags:
            p.flags[p.flags.index("table_needs_review")] = "table_reviewed"
    def as_region(e, block):
        return TextRegion("table", block, table={
            "schema": "reviewed-cells-v1",
            "reviewed": True, "engine": "table_transcriptions", "source": "data/structured/table_transcriptions.json",
            "title": _cell(e["title"])[0] if e.get("title") else None, "pages": e["pages"],
            "unit": _cell(e["unit"])[0] if e.get("unit") else None,
            "columns": [_cell(c)[0] for c in e["columns"]],
            "rows": [[_cell(c)[0] if c is not None else None for c in row] for row in e["rows"]],
            "context_lines": [l for l in [_cell(e["title"])[0] if e.get("title") else None,
                                          _cell(e["unit"])[0] if e.get("unit") else None] if l]})

    for p in pages:
        items = blocks.get(p.page, [])
        if getattr(p, "regions", None) and p.page in final:
            if p.page in text_order:
                p.regions = []      # 원문 줄 순서만 확인한 쪽: 자동 추출 표 후보 대신 원래 쪽 본문
            else:
                # 이 쪽의 표는 모두 기록에 있다(이 쪽 또는 앞 쪽에서 이어진 표). 자동 추출 표 영역은 뺀다
                p.regions = [r for r in p.regions if r.kind == "prose"] + [as_region(e, b) for e, b in items]
                continue
        if items:
            p.lines = [l for _, block in items for l in block] + p.lines
    return {"reviewer": review.get("reviewer"), "reviewed_on": review.get("reviewed_on"),
            "method": review.get("method"), "applied": applied, "rejected": rejected,
            "digest": hashlib.sha256(json.dumps(review, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
