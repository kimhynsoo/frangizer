"""합성 레시피 PDF를 RDB 전달용 구조화 JSON으로 만든다 (초안).

입력: data/manifest.json에 등록된 demo-cafe-recipes 판본 PDF
출력: data/structured/demo_cafe_recipes.json

  python3 rag/app/indexing/recipe_structure.py          # 생성 + 원문 대조
  python3 rag/app/indexing/recipe_structure.py --check  # 기존 출력 파일만 원문 대조

값·단위는 원문 그대로 둔다. 단위 환산·반올림·펌프 환산을 하지 않는다.
최종 컬럼·파일 형식은 C(RDB 스키마)와 합의 전이며 이 출력은 초안이다.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "data" / "manifest.json"
OUTPUT = ROOT / "data" / "structured" / "demo_cafe_recipes.json"
DOC_ID = "demo-cafe-recipes"
TZ = "+09:00"

STEP_RE = re.compile(r"^(\d+)\. (.*)$")
MENU_ID_RE = re.compile(r"메뉴 ID (\w+)")
TITLE_RE = re.compile(r"^(.+?)(?: \(v[\d.]+, 적용 (?:예정|중)\))?$")
SECTION_END = {"주문·제조 확인사항", "이 메뉴의 판본 이력"}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# 이 PDF는 단어 중간에서도 줄을 바꾸고(예: `의|미가`) 줄 끝 공백을 남기지 않는다.
# 줄바꿈 자리에 띄어쓰기가 있었는지는 텍스트로 알 수 없어, 사람이 검수한 자리만 띄운다.
# (앞 줄 마지막 어절, 다음 줄 첫 어절) — 2026-10-23 A 검수
SPACE_AT_WRAP = {("환산을", "적용하지"), ("메뉴의", "당도"), ("만들어", "답하지")}
WRAP_JOINS = []


def join_line(prev, line):
    key = (prev.split()[-1], line.split()[0])
    spaced = key in SPACE_AT_WRAP
    WRAP_JOINS.append({"at": f"{key[0]}|{key[1]}", "joined_with_space": spaced})
    return prev + (" " if spaced else "") + line


def join_wrapped(lines):
    """PDF 줄바꿈으로 잘린 문장을 이어 붙인다. 문장 끝(`.`)에서만 끊는다."""
    out = []
    for line in lines:
        if out and not out[-1].endswith("."):
            out[-1] = join_line(out[-1], line)
        else:
            out.append(line)
    return out


def parse_variant(lines, i):
    """`재료/계량/단위` 표와 번호 단계를 읽는다. 다음 HOT/ICED 또는 확인사항 전까지."""
    assert lines[i : i + 3] == ["재료", "계량", "단위"], lines[i : i + 3]
    i += 3
    ingredients = []
    while not STEP_RE.match(lines[i]):
        name, amount, unit = lines[i : i + 3]
        ingredients.append({"order": len(ingredients) + 1, "name": name, "amount": amount, "unit": unit})
        i += 3
    steps = []
    while lines[i] not in SECTION_END and lines[i] not in ("HOT", "ICED"):
        m = STEP_RE.match(lines[i])
        if m:
            steps.append({"order": int(m.group(1)), "text": m.group(2)})
        else:
            steps[-1]["text"] = join_line(steps[-1]["text"], lines[i])
        i += 1
    return ingredients, steps, i


def parse_menu_page(text):
    lines = [l.strip() for l in text.replace("\xa0", " ").splitlines() if l.strip()]
    m = MENU_ID_RE.search(text)
    if not m:
        return None
    page = {"menu_id": m.group(1), "display_name": TITLE_RE.match(lines[1]).group(1), "variants": {}, "notes": []}
    i = 2
    while lines[i] not in SECTION_END:
        if lines[i] in ("HOT", "ICED"):
            temp = lines[i]
            ingredients, steps, i = parse_variant(lines, i + 1)
            page["variants"][temp] = {"ingredients": ingredients, "steps": steps}
        else:
            i += 1
    j = lines.index("이 메뉴의 판본 이력")
    page["notes"] = join_wrapped(lines[lines.index("주문·제조 확인사항") + 1 : j])
    return page


def parse_common_rules(text):
    lines = [l.strip() for l in text.replace("\xa0", " ").splitlines() if l.strip()]
    rules, cur = [], None
    for line in lines:
        m = re.match(r"^(\d{2}) (.*)$", line)
        if m:
            cur = {"no": m.group(1), "text": m.group(2)}
            rules.append(cur)
        elif cur and not cur["text"].endswith("."):
            cur["text"] = join_line(cur["text"], line)
    return rules


def load_versions():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    docs = [d for d in manifest["documents"] if d["doc_id"] == DOC_ID]
    return sorted(docs, key=lambda d: [int(x) for x in d["version"].split(".")])


def to_ts(value, end=False):
    """manifest 날짜를 시간대 포함 시각으로. effective_to(포함)는 다음 날 0시(미포함)로 바꾼다."""
    if value is None:
        return None
    if "T" in value:
        return value
    if end:
        from datetime import date, timedelta
        value = (date.fromisoformat(value) + timedelta(days=1)).isoformat()
    return f"{value}T00:00:00{TZ}"


def build():
    versions = load_versions()
    menus, records, rules = {}, {}, None
    sources = []
    for doc in versions:
        path = ROOT / "data" / doc["path"]
        sources.append({
            "version": doc["version"], "path": f"data/{doc['path']}", "status": doc["status"],
            "sha256": sha256(path),
            "effective_from": to_ts(doc["effective_from"]),
            "effective_until": to_ts(doc["effective_to"], end=True),
        })
        pdf = pymupdf.open(path)
        original_pages = doc.get("derived_from", {}).get("original_pages")
        for idx, pg in enumerate(pdf):
            text = pg.get_text()
            src = {"document_version": doc["version"], "path": f"data/{doc['path']}", "page": idx + 1,
                   "printed_page": original_pages[idx] if original_pages else idx + 1}
            if rules is None and "공통 계량과 주문 옵션" in text:
                rules = {"rules": parse_common_rules(text.split("공통 계량과 주문 옵션", 1)[1].split("메뉴별 본문과", 1)[0]),
                         "source": src}
            page = parse_menu_page(text)
            if not page:
                continue
            menus.setdefault(page["menu_id"], {"menu_id": page["menu_id"], "display_name": page["display_name"]})
            for temp, body in page["variants"].items():
                key = (page["menu_id"], temp)
                content = {**body, "notes": page["notes"]}
                history = records.setdefault(key, [])
                if history and history[-1]["_content"] == content:
                    history[-1]["document_versions"].append(doc["version"])
                    history[-1]["sources"].append(src)
                else:
                    history.append({"_content": content, "first_version": doc["version"],
                                    "document_versions": [doc["version"]], "sources": [src]})

    by_version = {s["version"]: s for s in sources}
    recipe_versions = []
    for (menu_id, temp), history in records.items():
        prev_id = None
        for n, h in enumerate(history):
            nxt = history[n + 1]["first_version"] if n + 1 < len(history) else None
            rec_id = f"{menu_id}-{temp.lower()}-{h['first_version']}"
            recipe_versions.append({
                "recipe_version_id": rec_id,
                "menu_id": menu_id,
                "temperature": temp,
                "serving_basis": "1잔",
                "effective_from": by_version[h["first_version"]]["effective_from"],
                "effective_until": by_version[nxt]["effective_from"] if nxt else None,
                "previous_recipe_version_id": prev_id,
                "document_versions": h["document_versions"],
                "ingredients": h["_content"]["ingredients"],
                "steps": h["_content"]["steps"],
                "order_notes": h["_content"]["notes"],
                "sources": h["sources"],
            })
            prev_id = rec_id

    # v1.0 메뉴는 문서 v1.0 적용 종료일이 아니라 다음 판본 등장 전까지 유효하다.
    # 다음 판본이 없으면 effective_until=null (종료 시점 미지정).
    return {
        "schema": "frangizer.recipe-structured.draft",
        "schema_version": 0,
        "review_status": "draft",
        "doc_id": DOC_ID,
        "is_synthetic": True,
        "is_demo": True,
        "brand": None,
        "conventions": {
            "amount": "원문 문자열 그대로. 단위 환산·반올림 없음",
            "effective_until": "미포함(이 시각부터 적용 안 됨). null이면 종료 시점 미지정",
            "page": "PDF 파일 기준 1부터 시작. printed_page는 쪽 하단 인쇄 번호(합본 기준)",
            "order_notes": "해당 메뉴 쪽의 '주문·제조 확인사항' 원문. HOT·ICED 공통 문장일 수 있음",
            "aliases": "원문에 별칭 근거가 있는 경우만 기록",
        },
        "source_documents": sources,
        "common_rules": rules,
        "menus": [
            {**m, "aliases": ["데모 바닐라라테"] if m["menu_id"] == "vanilla_latte" else []}
            for m in menus.values()
        ],
        "recipe_versions": recipe_versions,
        "review": {
            "wrap_joins": sorted({(j["at"], j["joined_with_space"]) for j in WRAP_JOINS}),
            "wrap_joins_note": "PDF 줄바꿈을 이어 붙인 자리. 띄어쓰기 여부는 SPACE_AT_WRAP 검수 목록으로 결정",
            "open_questions": [
                "최종 컬럼·파일 형식은 C의 RDB 스키마 확정 후 맞춘다",
                "v1.1은 staged(미배포) 판본이다. RDB 적재 시 배포 전 노출 방지는 서버 상태로 처리해야 한다",
                "펌프 환산(1회 5 mL)은 공통 규칙 03에 근거가 있으나 구조화 필드로 만들지 않고 원문 문장으로만 둔다",
            ],
        },
    }


def check(data):
    """모든 값·단위·단계·확인사항이 인용한 쪽 원문에 실제로 있는지 대조한다."""
    pages = {}
    def page_text(src):
        key = (src["path"], src["page"])
        if key not in pages:
            pages[key] = re.sub(r"\s+", "", pymupdf.open(ROOT / src["path"])[src["page"] - 1].get_text())
        return pages[key]

    errors, checked = [], 0
    for s in data["source_documents"]:
        if sha256(ROOT / s["path"]) != s["sha256"]:
            errors.append(f"sha256 불일치: {s['path']}")
    for r in data["recipe_versions"]:
        for src in r["sources"]:
            t = page_text(src)
            if r["menu_id"] not in t:
                errors.append(f"{r['recipe_version_id']}: 메뉴 ID 없음 {src}")
            for ing in r["ingredients"]:
                checked += 1
                if re.sub(r"\s+", "", f"{ing['name']}{ing['amount']}{ing['unit']}") not in t:
                    errors.append(f"{r['recipe_version_id']}: 재료 불일치 {ing} @p{src['page']}")
            for item in r["steps"] + [{"text": n} for n in r["order_notes"]]:
                checked += 1
                if re.sub(r"\s+", "", item["text"]) not in t:
                    errors.append(f"{r['recipe_version_id']}: 문장 불일치 '{item['text'][:30]}' @p{src['page']}")
    rs = data["common_rules"]
    t = page_text(rs["source"])
    for rule in rs["rules"]:
        checked += 1
        if re.sub(r"\s+", "", rule["no"] + rule["text"]) not in t:
            errors.append(f"공통 규칙 {rule['no']} 불일치")
    return checked, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="출력 파일을 다시 만들지 않고 원문 대조만 한다")
    args = ap.parse_args()
    if args.check:
        data = json.loads(OUTPUT.read_text(encoding="utf-8"))
    else:
        data = build()
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checked, errors = check(data)
    print(f"menus={len(data['menus'])} recipe_versions={len(data['recipe_versions'])} checked={checked} errors={len(errors)}")
    for e in errors:
        print("  -", e)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
