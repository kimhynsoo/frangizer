"""시연·평가에 쓰는 조문만 시점 검수한 기록을 조각에 반영한다 (QA: [시행일 미검수]).

검수 기록: data/structured/legal_reviews.json — 원본 sha256 + 조문 표기(예: 제12조의2)
문서 단위 legal_temporal_reviewed(IndexJobRequest)는 그대로 false로 두고, 검수한 조문의 조각만
chunks.jsonl review.legal_temporal_reviewed=true, review.legal_temporal_scope="article"로 표시한다.

색인 때 다시 확인하는 조건(하나라도 어긋나면 적용하지 않고 품질 보고서에 남긴다):
- 조문이 실제로 있다
- 자동 판단 못 한 시행일 표기(temporal_note)가 없다
- 시행 예정본으로 나뉜 조문(-from 조각)이 있으면 기록에 future_versions_confirmed=true가 있어야 한다
- 출력본 부칙에 그 조문 언급이 없다(있으면 기록에 addenda_checked=true와 메모가 있어야 한다)
"""
import hashlib
import json
import os
import re
from pathlib import Path

FILE_NAME = "legal_reviews.json"


def review_file():
    if os.environ.get("LEGAL_REVIEW_FILE"):
        return Path(os.environ["LEGAL_REVIEW_FILE"])
    if os.environ.get("RECIPE_STRUCTURED_FILE"):
        p = Path(os.environ["RECIPE_STRUCTURED_FILE"]).parent / FILE_NAME
        if p.exists():
            return p
    p = Path(__file__).resolve().parents[3] / "data" / "structured" / FILE_NAME
    return p if p.exists() else None


def load_reviews(sha256, path=None):
    path = path or review_file()
    if not path or not Path(path).exists():
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return next((d for d in data.get("documents", []) if d.get("sha256") == sha256), None)


def _mentions(text, article):
    """부칙 본문에서 '제12조의2' 같은 조문 언급(뒤에 '의N'이 더 붙은 다른 조는 제외)."""
    return re.search(re.escape(article) + r"(?!의\d)", text) is not None


def check_article(chunks, entry):
    """조문 하나의 확인 결과: (통과 여부, 근거 dict)."""
    art = entry["article"]
    own = [c for c in chunks if c.article == art]
    addenda = [c for c in chunks if c.section_key.startswith("add") and c.article != art]
    evidence = {
        "chunks": [c.section_key for c in own],
        "pages": sorted({c.page for c in own if c.page}),
        "future_versions": sorted({c.effective_from for c in own if c.effective_from}),
        "temporal_notes": sorted({c.temporal_note for c in own if c.temporal_note}),
        "addenda_mentions": sorted({c.section_key.split(".")[0] for c in addenda if _mentions(c.text, art)}),
    }
    problems = []
    if not own:
        problems.append("조문이 없음")
    if evidence["temporal_notes"]:
        problems.append("자동 판단 못 한 시행일 표기가 있음")
    if evidence["future_versions"] and not entry.get("future_versions_confirmed"):
        problems.append("시행 예정본이 있으나 확인 기록 없음")
    if evidence["addenda_mentions"] and not entry.get("addenda_checked"):
        problems.append("부칙 언급이 있으나 확인 기록 없음")
    evidence["problems"] = problems
    return not problems, evidence


def reviewed_articles(chunks, review):
    """(검수 통과 조문 집합, 보고서 요약)."""
    if not review:
        return set(), None
    ok, applied, rejected = set(), [], []
    for entry in review["articles"]:
        passed, ev = check_article(chunks, entry)
        (applied if passed else rejected).append({"article": entry["article"], **ev})
        if passed:
            ok.add(entry["article"])
    return ok, {"reviewer": review.get("reviewer"), "reviewed_on": review.get("reviewed_on"),
                "applied": applied, "rejected": rejected,
                "digest": hashlib.sha256(json.dumps(review, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
