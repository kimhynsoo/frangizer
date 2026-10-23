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
