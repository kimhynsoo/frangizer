"""Local table extraction. Parsed cells are candidates until a source-bound review approves them."""
import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

PARSER_VERSION = 'local-tables-v1'


def review_path():
    return Path(os.environ.get('TABLE_REVIEW_FILE') or
                Path(__file__).resolve().parents[3] / 'data/structured/table_reviews.json')


def reviews_for(source_sha):
    try:
        data = json.loads(review_path().read_text(encoding='utf-8'))
        return [r for r in data.get('reviews', []) if r.get('source_sha256') == source_sha]
    except (OSError, ValueError, TypeError, AttributeError):
        return []  # Malformed/unavailable review data never approves a table.


def extraction_signature(source_sha):
    body = {'parser': PARSER_VERSION, 'reviews': reviews_for(source_sha)}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def table_digest(table):
    keys = ('source_sha256', 'page', 'bbox', 'engine', 'rows', 'parser_version')
    return hashlib.sha256(json.dumps({k: table[k] for k in keys}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def normalize_rows(rows):
    return [[' '.join((cell or '').split()) for cell in row] for row in rows if any(row)]


def candidate_score(rows, raw):
    if len(rows) < 2 or max(map(len, rows), default=0) < 2:
        return -1
    original = Counter(''.join(raw.split()))
    extracted = Counter(''.join(''.join(c.split()) for row in rows for c in row))
    return sum((original & extracted).values()) / max(1, sum(original.values()))


def extract_local_tables(path, page, boxes, height, raw_tables, context_lines, source_sha):
    """Use detected inner regions, excluding decorative page borders. No LLM/OCR call."""
    candidates = [[] for _ in boxes]
    try:
        import camelot
        areas = [','.join(map(str, (x0, height-y0, x1, height-y1))) for x0, y0, x1, y1 in boxes]
        parsed = camelot.read_pdf(str(path), pages=str(page), flavor='lattice', table_areas=areas,
                                  copy_text=['h', 'v'], suppress_stdout=True)
        for table in parsed:
            a, b, c, d = table._bbox  # Camelot's PDF coordinate rectangle, bottom-left origin.
            converted = (a, height-d, c, height-b)
            distance = lambda box: sum(abs(x-y) for x, y in zip(box, converted))
            index = min(range(len(boxes)), key=lambda i: distance(boxes[i]))
            if distance(boxes[index]) <= 20:
                candidates[index].append(('camelot-lattice', normalize_rows(table.df.values.tolist())))
    except Exception:
        pass  # The fallback preserves the original unreviewed text if both adapters fail.

    # PDFPlumber uses native text coordinates; compare preservation on each detected area.
    try:
        import pdfplumber
        with pdfplumber.open(path) as doc:
            for i, box in enumerate(boxes):
                cropped = doc.pages[page-1].crop(box)
                rows = cropped.extract_table({'vertical_strategy': 'lines', 'horizontal_strategy': 'lines'})
                if rows:
                    candidates[i].append(('pdfplumber-lines', normalize_rows(rows)))
    except Exception:
        pass

    result = []
    for i, variants in enumerate(candidates):
        raw = '\n'.join(raw_tables[i])
        if not variants:
            result.append(None)
            continue
        engine, rows = max(variants, key=lambda v: candidate_score(v[1], raw))
        score = candidate_score(rows, raw)
        if score < 0:
            result.append(None)
            continue
        table = {'source_sha256': source_sha, 'page': page, 'bbox': list(boxes[i]),
                 'engine': engine, 'rows': rows, 'context_lines': context_lines,
                 'text_coverage': round(score, 4), 'parser_version': PARSER_VERSION}
        # Coverage only finds dropped characters; it does NOT prove correct row/column associations.
        table['table_sha256'] = table_digest(table)
        table['reviewed'] = any(
            r.get('table_sha256') == table['table_sha256'] and r.get('reviewed') is True
            and r.get('reviewed_by') and r.get('reviewed_at') for r in reviews_for(source_sha)
        )
        result.append(table)
    return result


def table_lines(table):
    """Each row carries column names; merged/multi-level headers still require manual review."""
    rows = table['rows']
    headings = rows[0]
    return [f'[표 / {table["engine"]}]'] + [
        '[행 %d] ' % i + ' | '.join(
            f'{headings[n] if n < len(headings) and headings[n] else "열"+str(n+1)}: {value}'
            for n, value in enumerate(row)
        ) for i, row in enumerate(rows[1:], 1)
    ]


def main():
    ap = argparse.ArgumentParser(description='검수용 표 후보를 내보냅니다. 승인·색인·배포는 하지 않습니다.')
    ap.add_argument('pdf', type=Path)
    ap.add_argument('--pages', nargs='+', type=int, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    from .extract import extract_pdf
    pages, _ = extract_pdf(args.pdf, set(args.pages))
    tables = [r.table for p in pages for r in p.regions if r.table]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'tables': tables}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'표 후보 {len(tables)}개: {args.output} (자동 검수 완료 처리 없음)')


if __name__ == '__main__':
    main()
