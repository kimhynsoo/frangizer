"""자료 종류별 분할.

- 법령·고시 본문: 조(제N조, 제N조의M) 단위. 부칙은 부칙마다 따로 번호를 매긴다.
  긴 조는 항(①②…) 경계에서 나누고 조 제목을 각 조각 앞에 다시 붙인다.
- 별표·표·지침처럼 조가 없는 부분, 정보공개서, 레시피: 쪽 단위. 긴 쪽은 줄 경계에서 나누고
  쪽 첫 줄(제목)을 각 조각 앞에 다시 붙인다.
- 모든 조각은 시작 쪽(page)과 section_key를 가진다. 같은 입력·같은 CHUNKER_VERSION이면
  같은 조각이 나온다.

section_key·chunk_id 형식은 B와 합의 전 초안이다.
"""
import re
from dataclasses import dataclass

CHUNKER_VERSION = "chunker-v1"
MAX_CHARS = 1500

ARTICLE_RE = re.compile(r"^제(\d+)조(?:의(\d+))?(?:\s?\(([^)]*)\)|\s+삭제)")
ADDENDA_RE = re.compile(r"^부칙\s*<")
ANNEX_RE = re.compile(r"^■.*\[별표")
PARAGRAPH_RE = re.compile(r"^[①-⑳]")
CHAPTER_RE = re.compile(r"^제\d+(?:장|절|관)(?:의\d+)?\s+\S")
# 법제처 출력본은 시행 전 개정 조문을 현행 조문 뒤에 다시 싣고 끝에 이 꼬리표를 붙인다.
FUTURE_TRAILER_RE = re.compile(r"\[시행일\s*:\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\]\s*(제\d+조(?:의\d+)?)(\S*)\s*$")


@dataclass
class Chunk:
    section_key: str
    page: int | None
    article: str | None
    heading: str
    text: str
    part: int = 1
    effective_from: str | None = None   # 'YYYY-MM-DD'. None이면 문서 적용일
    effective_until: str | None = None  # 'YYYY-MM-DD'(미포함). None이면 종료 미지정
    temporal_note: str | None = None    # 자동 판단하지 못한 조문별 시행일 표기


def _split_long(lines, heading, max_chars, boundary=None):
    """lines를 max_chars 안쪽으로 나눈다. boundary(line)가 참인 줄에서 우선 끊는다."""
    parts, cur, size = [], [], 0
    for line in lines:
        at_boundary = boundary(line) if boundary else True
        if cur and size + len(line) > max_chars and at_boundary:
            parts.append(cur)
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        parts.append(cur)
    out = []
    for n, part in enumerate(parts):
        body = part if n == 0 or not heading else [f"{heading} (이어서)"] + part
        out.append(body)
    return out


def _article_label(m):
    return f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")


def _article_key(m, addenda_no):
    key = f"art-{m.group(1)}" + (f"-{m.group(2)}" if m.group(2) else "")
    return f"add{addenda_no}-{key}" if addenda_no else key


def chunk_articles(pages, max_chars=MAX_CHARS):
    """법령형 문서. 반환: (조 단위 조각, 조가 아닌 나머지 쪽 줄[(page, lines)])."""
    sections, rest = [], []
    cur = None
    addenda_no = 0
    in_annex = False
    chapter = None
    for p in pages:
        leftover = []
        for line in p.lines:
            if ANNEX_RE.match(line):
                in_annex = True
                cur = None
            if in_annex:
                leftover.append(line)
                continue
            if CHAPTER_RE.match(line) and (cur is not None or sections):
                chapter = line  # 장·절 제목은 앞 조문 본문에 넣지 않고 다음 조문 제목 맥락으로
                continue
            if ADDENDA_RE.match(line):
                addenda_no += 1
                cur = {"key": f"add{addenda_no}", "page": p.page, "article": None,
                       "heading": line, "lines": [line]}
                sections.append(cur)
                continue
            m = ARTICLE_RE.match(line)
            if m:
                label = _article_label(m)
                if addenda_no:
                    label = f"부칙{addenda_no} {label}"
                title = line.split(")")[0] + ")" if m.group(3) is not None else label
                cur = {"key": _article_key(m, addenda_no), "page": p.page, "article": label,
                       "heading": f"{chapter} > {title}" if chapter else title,
                       "lines": [line]}
                sections.append(cur)
            elif cur is not None:
                cur["lines"].append(line)
            else:
                leftover.append(line)  # 첫 조 이전 머리말
        if leftover:
            rest.append((p.page, leftover))

    _assign_article_periods(sections)
    chunks, seen = [], {}
    for s in sections:
        key = s["key"]
        seen[key] = seen.get(key, 0) + 1
        if s.get("from"):
            key = f"{key}-from{s['from'].replace('-', '')}"
        elif seen[key] > 1:  # 같은 번호가 근거 없이 다시 나오면(발췌·중복 출력) 순번을 붙여 구분
            key = f"{key}-dup{seen[key]}"
        parts = _split_long(s["lines"], s["heading"], max_chars, boundary=lambda l: bool(PARAGRAPH_RE.match(l)))
        for n, body in enumerate(parts, 1):
            chunks.append(Chunk(section_key=key if len(parts) == 1 else f"{key}.p{n}",
                                page=s["page"], article=s["article"], heading=s["heading"],
                                text="\n".join(body), part=n, effective_from=s.get("from"),
                                effective_until=s.get("until"), temporal_note=s.get("note")))
    return chunks, rest


def _assign_article_periods(sections):
    """시행일 꼬리표가 붙은 조문 → 그 날부터 적용.
    - 앞에 같은 조(현행본)가 있으면: 꼬리표가 일부 항을 가리켜도 뒤의 것은 '그 날부터의 조문 전체'
      이므로 나누고, 현행본은 그 날까지로 한다.
    - 앞에 같은 조가 없고 꼬리표가 조 전체를 가리키면: 시행 예정 신설 조문.
    - 앞에 같은 조가 없고 일부 항만 가리키면: 자동 판단하지 않고 note로 남긴다."""
    last_by_key = {}
    for s in sections:
        m = next((x for x in map(FUTURE_TRAILER_RE.search, reversed(s["lines"][-3:])) if x), None)
        label = (s["article"] or "").split(" ")[-1]
        prev = last_by_key.get(s["key"])
        if m:
            day = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            whole_article = m.group(4) == label and not m.group(5)
            if m.group(4) == label and prev is not None and not prev.get("until"):
                s["from"] = day
                prev["until"] = day
            elif whole_article and prev is None:
                s["from"] = day
            else:
                s["note"] = f"시행일 {day} 표기({m.group(4)}{m.group(5)}) — 적용 범위 수동 검수 필요"
        last_by_key[s["key"]] = s


def chunk_pages(page_lines, max_chars=MAX_CHARS, prefix="p"):
    """[(page, lines)] → 쪽 단위 조각. 쪽 시작 위치를 보존한다."""
    chunks = []
    for page, lines in page_lines:
        if not lines:
            continue
        heading = lines[0][:80]
        parts = _split_long(lines, heading, max_chars)
        for n, body in enumerate(parts, 1):
            base = f"{prefix}{page}" if page is not None else prefix
            chunks.append(Chunk(section_key=base if len(parts) == 1 else f"{base}.{n}",
                                page=page, article=None, heading=heading,
                                text="\n".join(body), part=n))
    return chunks


def chunk_document(pages, category, material_type, max_chars=MAX_CHARS):
    """자료 종류에 맞는 분할 방식을 고른다."""
    if material_type == "official" or (material_type == "synthetic" and category != "recipe"):
        articles, rest = chunk_articles(pages, max_chars)
        if len(articles) >= 3 or (articles and material_type == "official"):
            return articles + chunk_pages(rest, max_chars, prefix="p"), "articles+pages"
    return chunk_pages([(p.page, p.lines) for p in pages], max_chars), "pages"
