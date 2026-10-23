"""Explicit, source-bound approval of table candidates AFTER comparing them with the PDF."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .tables import review_path, table_digest


def main():
    ap = argparse.ArgumentParser(description='PDF 원문과 대조한 표만 검수 완료로 기록합니다. 자동 검수가 아닙니다.')
    ap.add_argument('candidates', type=Path)
    ap.add_argument('--table-sha', help='표 후보의 table_sha256')
    ap.add_argument('--reviewer')
    ap.add_argument('--approve', action='store_true', help='원문 대조를 마친 선택 표를 승인')
    ap.add_argument('--review-file', type=Path, default=review_path())
    args = ap.parse_args()
    tables = json.loads(args.candidates.read_text(encoding='utf-8'))['tables']
    if not args.approve:
        for t in tables:
            print(f'PDF {t["page"]}쪽 | {t["engine"]} | {len(t["rows"])}행 | {t["table_sha256"]}')
        return
    if not args.table_sha or not args.reviewer or not args.reviewer.strip():
        ap.error('--approve에는 --table-sha와 --reviewer가 필요합니다')
    chosen = [t for t in tables if t['table_sha256'] == args.table_sha]
    if len(chosen) != 1:
        ap.error('정확히 한 표의 SHA를 선택하세요')
    t = chosen[0]
    if table_digest(t) != t['table_sha256']:
        ap.error('후보 내용과 SHA가 다릅니다. 추출 후보를 다시 만들어 주세요')
    record = {k: t[k] for k in ('source_sha256', 'page', 'table_sha256')}
    record.update(reviewed=True, reviewed_by=args.reviewer.strip(), reviewed_at=datetime.now(timezone.utc).isoformat())
    data = json.loads(args.review_file.read_text(encoding='utf-8')) if args.review_file.exists() else {'schema_version': 1, 'reviews': []}
    data['reviews'] = [r for r in data['reviews'] if r.get('table_sha256') != t['table_sha256']] + [record]
    args.review_file.parent.mkdir(parents=True, exist_ok=True)
    args.review_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'PDF {t["page"]}쪽 표 검수 기록 완료. 적용하려면 새 job_id로 재색인해야 합니다.')


if __name__ == '__main__':
    main()
"""사람이 PDF 원본과 대조한 표 검수 기록을 색인에 반영한다 (QA-03).

검수 기록: data/structured/table_reviews.json (rag 컨테이너에는 data/structured가 읽기 전용으로 연결된다)
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
"""
import hashlib
import json
import os
import re
from pathlib import Path

REVIEW_MARK = "[검수된 표 · PDF 원본 대조]"
FILE_NAME = "table_reviews.json"
EDGE_RE = re.compile(r"^(㈜더본코리아-빽다방-\d+ 접수본|페이지 \d+ / \d+)$")


def review_file():
    """TABLE_REVIEW_FILE > RECIPE_STRUCTURED_FILE과 같은 폴더 > 저장소 data/structured. 없으면 None."""
    if os.environ.get("TABLE_REVIEW_FILE"):
        return Path(os.environ["TABLE_REVIEW_FILE"])
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
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return next((d for d in data.get("documents", []) if d.get("sha256") == sha256), None)


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
        label, rest = _cell(row[0])[0], []
        for name, value in zip(cols[1:], row[1:]):
            if value:
                rest.append(f"{name} {_cell(value)[0]}")
        out.append(f"- {label}: " + " · ".join(rest))
    return out


def apply_reviews(pages, raw_pages, review):
    """pages(PageText 목록)에 검수 결과를 반영. 보고서용 요약을 돌려준다."""
    if not review:
        return None
    by_page = {p.page: p for p in pages}
    applied, rejected, reviewed_pages = [], [], set()
    for entry in review["tables"]:
        errors = validate(entry, raw_pages)
        if errors:
            rejected.append({"pages": entry["pages"], "covers": entry.get("covers", entry["pages"]),
                             "title": entry.get("title"), "errors": errors[:5]})
            continue
        covers = entry.get("covers", entry["pages"])
        reviewed_pages.update(covers)
        anchor = by_page.get(covers[0])
        block = render(entry)
        if anchor is not None and block:
            anchor.lines = block + anchor.lines
        applied.append({"pages": entry["pages"], "title": entry.get("title"), "rows": len(entry.get("rows") or [])})
    # 검증에 실패한 기록이 걸친 쪽은 표시를 바꾸지 않는다
    rejected_pages = {p for r in rejected for p in r["covers"]}
    for no in reviewed_pages - rejected_pages:
        p = by_page.get(no)
        if p is not None and "table_needs_review" in p.flags:
            p.flags[p.flags.index("table_needs_review")] = "table_reviewed"
    return {"reviewer": review.get("reviewer"), "reviewed_on": review.get("reviewed_on"),
            "method": review.get("method"), "applied": applied, "rejected": rejected,
            "digest": hashlib.sha256(json.dumps(review, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
