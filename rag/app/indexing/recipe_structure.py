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
    page["history"] = parse_history(lines[j + 4 : -2])  # 머리글(판본·적용·내용) 다음부터 꼬리말 전까지
    return page


def parse_history(lines):
    """판본 이력 표: `v1.0 | 적용 | 내용` 행. 적용·내용 칸의 줄바꿈은 이어 붙인다."""
    rows, cur = {}, None
    for line in lines:
        if re.fullmatch(r"v\d+\.\d+", line):
            cur = rows.setdefault(line[1:], [])
        elif cur is not None:
            cur.append(line)
    out = {}
    for ver, cells in rows.items():
        applied = cells[0]
        rest = cells[1:]
        if applied.endswith("-") and rest:  # `2026-10-01 ~ 2026-10-` + `25`
            applied += rest.pop(0)
        out[ver] = {"applied": applied, "content": "".join(rest)}
    return out


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
    docs = [d for d in manifest["documents"] if d["local_key"] == DOC_ID]
    return sorted(docs, key=lambda d: [int(x) for x in d["upload_metadata"]["version"].split(".")])


# 확인사항 문장 → 옵션·미지원 사항. 원문에 적힌 값만 뽑고, 패턴에 안 맞는 문장은 info로 남긴다.
ICE_LESS_RE = re.compile(r"얼음 적게(?: 주문)?(?:은|는) (\d+) g")
ICE_NONE_RE = re.compile(r"얼음 없음은 (\d+) g")
LESS_SWEET_RE = re.compile(r"덜 달게는 시럽 (\d+) mL, 전용 펌프 (\d+)회")
NO_SYRUP_RE = re.compile(r"시럽 빼기는 (\d+) mL")
BASE_PUMP_RE = re.compile(r"기본 시럽 (\d+) mL는 전용 펌프 (\d+)회")
CONSTRAINT_RE = re.compile(
    r"지원하지 않는다|기준은 없다|기준이 없다|등록되지 않았다|정하지 않는다|임의로 \S+ 않는다|환산하지 않는다"
    r"|보충하지 않는다|제조 순서가 아니다|의미는 아니다|포함되지 않는다|시럽이 없다|기본 구성에 없")
# 공통 규칙 번호 → 종류. 문구가 바뀌면 check()의 keyword 대조에서 걸린다.
COMMON_RULE_KIND = {
    "01": ("basis", "1잔 기준"), "02": ("basis", "1샷은"), "03": ("conversion", "펌프 1회는 5 mL"),
    "04": ("basis", "기본 계량"), "05": ("option", "얼음 적게"), "06": ("option", "샷 추가 1회"),
    "07": ("option", "덜 달게"), "08": ("unsupported", "대체유"), "09": ("unsupported", "보장하지 않는다"),
    "10": ("unsupported", "환불 정책"), "11": ("unsupported", "없는 메뉴"), "12": ("versioning", "적용일"),
}
# 공통 규칙에서 값이 명시된 옵션만. 메뉴별 값이 있으면 메뉴별 값이 우선이다.
COMMON_OPTIONS = [
    {"name": "샷 추가", "rule": "06", "per_order": True,
     "change": {"ingredient": "에스프레소", "delta": "30", "unit": "mL"}, "phrase": "샷 추가 1회는 에스프레소 30 mL를 더한다"},
]


def split_sentences(text):
    return [s for s in re.split(r"(?<=다\.)\s+", text) if s]


def structure_notes(notes, ingredients):
    """메뉴 쪽 확인사항 → {options, pumps, constraints, info}. 값은 원문 문자열, 근거 문장(text) 포함."""
    names = {i["name"]: i for i in ingredients}
    syrup = next((n for n in names if "시럽" in n), None)
    out = {"options": [], "pumps": [], "constraints": [], "info": []}
    for note in notes:
        if "얼음" not in names and (ICE_LESS_RE.search(note) or ICE_NONE_RE.search(note)):
            continue  # 얼음이 없는 온도(HOT)에는 얼음 옵션 문단(딸린 '기본량 유지' 문장 포함)을 붙이지 않는다
        for s in split_sentences(note):
            hit = False
            if "얼음" in names:
                for rx, name in ((ICE_LESS_RE, "얼음 적게"), (ICE_NONE_RE, "얼음 없음")):
                    m = rx.search(s)
                    if m:
                        out["options"].append({"name": name, "set": {"ingredient": "얼음", "amount": m.group(1), "unit": "g"},
                                               "text": s})
                        hit = True
            if syrup:
                m = LESS_SWEET_RE.search(s)
                if m:
                    out["options"].append({"name": "덜 달게", "set": {"ingredient": syrup, "amount": m.group(1), "unit": "mL",
                                                                   "pumps": m.group(2)}, "text": s})
                    hit = True
                m = NO_SYRUP_RE.search(s)
                if m:
                    out["options"].append({"name": "시럽 빼기", "set": {"ingredient": syrup, "amount": m.group(1), "unit": "mL"},
                                           "text": s})
                    hit = True
                m = BASE_PUMP_RE.search(s)
                if m:
                    if m.group(1) != names[syrup]["amount"]:
                        raise ValueError(f"펌프 문장의 기본 시럽({m.group(1)})과 재료표({names[syrup]['amount']})가 다름")
                    out["pumps"].append({"ingredient": syrup, "amount": m.group(1), "unit": "mL", "pumps": m.group(2),
                                         "text": s})
                    hit = True
            if CONSTRAINT_RE.search(s):
                out["constraints"].append({"text": s})
                hit = True
            if not hit:
                out["info"].append({"text": s})
    return out


def ingredient_changes(prev, cur):
    """직전 판본 대비 재료 변화. 값은 원문 문자열 그대로 비교한다."""
    before = {i["name"]: i for i in prev["ingredients"]}
    after = {i["name"]: i for i in cur["ingredients"]}
    changes = []
    for name in list(before) + [n for n in after if n not in before]:
        b, a = before.get(name), after.get(name)
        if b and a and (b["amount"], b["unit"]) == (a["amount"], a["unit"]):
            continue
        changes.append({
            "name": name,
            "before": None if not b else {"amount": b["amount"], "unit": b["unit"]},
            "after": None if not a else {"amount": a["amount"], "unit": a["unit"]},
        })
    return changes


def build():
    versions = load_versions()
    menus, records, rules = {}, {}, None
    sources = []
    for doc in versions:
        meta = doc["upload_metadata"]
        version = meta["version"]
        path = ROOT / "data" / doc["path"]
        sources.append({
            "version": version, "path": f"data/{doc['path']}", "repo_status": doc["repo_status"],
            "sha256": sha256(path),
            "effective_from": meta["effective_from"],
            "effective_until": doc["effective_until"],
            "change_summary": meta["change_summary"],
        })
        pdf = pymupdf.open(path)
        original_pages = doc.get("derived_from", {}).get("original_pages")
        for idx, pg in enumerate(pdf):
            text = pg.get_text()
            src = {"document_id": doc["server"]["document_id"], "version_id": doc["server"]["version_id"],
                   "document_version": version, "path": f"data/{doc['path']}", "page": idx + 1,
                   "printed_page": original_pages[idx] if original_pages else idx + 1}
            if rules is None and "공통 계량과 주문 옵션" in text:
                parsed = parse_common_rules(text.split("공통 계량과 주문 옵션", 1)[1].split("메뉴별 본문과", 1)[0])
                rules = {"rules": [{**r, "kind": COMMON_RULE_KIND[r["no"]][0]} for r in parsed],
                         "options": [{k: v for k, v in o.items() if k != "phrase"} | {"text": o["phrase"]}
                                     for o in COMMON_OPTIONS],
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
                    history[-1]["document_versions"].append(version)
                    history[-1]["sources"].append(src)
                else:
                    history.append({"_content": content, "first_version": version,
                                    "history_row": page["history"].get(version),
                                    "document_versions": [version], "sources": [src]})

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
                "order_rules": structure_notes(h["_content"]["notes"], h["_content"]["ingredients"]),
                "change": {
                    "history_row": h["history_row"],
                    "ingredient_changes": ingredient_changes(history[n - 1]["_content"], h["_content"]) if n else None,
                },
                "sources": h["sources"],
            })
            prev_id = rec_id

    # 메뉴 판본의 적용 기간은 문서 판본이 아니라 내용이 바뀐 시점으로 정한다.
    # 내용이 같은 메뉴는 v1.0 시작부터 다음 변경 전까지 한 건이며, 변경이 없으면 effective_until=null.
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
            "change": "history_row는 메뉴 쪽 '판본 이력' 표의 해당 판본 행 원문, ingredient_changes는 직전 메뉴 판본 대비 자동 비교(첫 판본은 null)",
            "aliases": "원문에 별칭 근거가 있는 경우만 기록",
            "order_rules": "order_notes를 문장 단위로 나눠 분류. options·pumps는 원문에 적힌 값만(set.amount는 그 옵션일 때의 재료량), "
                           "constraints는 미지원·금지·기준 없음, info는 그 밖의 안내. 얼음 옵션은 얼음이 있는 온도에만 붙인다",
            "common_rules.options": "메뉴별 order_rules에 같은 옵션이 있으면 메뉴별 값이 우선",
            "pumps": "원문에 펌프 횟수가 적힌 경우만. 1회 5 mL(공통 규칙 03)로 다른 값을 계산해 채우지 않는다",
            "exposure": "답변에 쓰려면 sources[].version_id가 질의의 allowed_versions에 있고 effective_from ≤ as_of < effective_until이어야 한다. "
                        "파일에 있다는 것만으로 배포된 판본이 아니다(v1.1은 staged)",
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
                "MVP는 RAG 내부 조회용(B). RDB 적재는 E2E 이후 확장에서 C와 형식 확정",
                "메뉴·옵션 해석(질문 → menu_id·temperature·옵션 이름)은 B 몫. 이 파일은 해석 결과로 값을 고르는 기준만 제공",
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
            row = r["change"]["history_row"]
            extra = [{"text": row["applied"] + row["content"]}] if row else []
            for item in r["steps"] + [{"text": n} for n in r["order_notes"]] + extra:
                checked += 1
                if re.sub(r"\s+", "", item["text"]) not in t:
                    errors.append(f"{r['recipe_version_id']}: 문장 불일치 '{item['text'][:30]}' @p{src['page']}")
        rules = r["order_rules"]
        for item in rules["options"] + rules["pumps"]:
            checked += 1
            v = item.get("set", item)
            if item["text"] not in " ".join(r["order_notes"]) or f"{v['amount']} {v['unit']}" not in item["text"]:
                errors.append(f"{r['recipe_version_id']}: 옵션 근거 불일치 {item}")
            if v["ingredient"] not in {i["name"] for i in r["ingredients"]}:
                errors.append(f"{r['recipe_version_id']}: 옵션 재료가 재료표에 없음 {v['ingredient']}")
    rs = data["common_rules"]
    t = page_text(rs["source"])
    for rule in rs["rules"]:
        checked += 1
        if re.sub(r"\s+", "", rule["no"] + rule["text"]) not in t:
            errors.append(f"공통 규칙 {rule['no']} 불일치")
        if COMMON_RULE_KIND[rule["no"]][1] not in rule["text"]:
            errors.append(f"공통 규칙 {rule['no']} 종류 키워드 불일치")
    for o in rs["options"]:
        checked += 1
        if re.sub(r"\s+", "", o["text"]) not in t:
            errors.append(f"공통 옵션 근거 불일치 {o['name']}")
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
