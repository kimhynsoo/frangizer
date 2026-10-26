"""본사 대시보드 시연용 합성 질문 기록 SQL 생성기(결정적).

  python3 backend/scripts/gen_demo_question_events.py > backend/src/main/resources/db/demo/R__demo_question_events.sql
"""
import random

random.seed(20261026)

REGIONS = {"SEOUL": "서울", "GYEONGGI": "경기", "CHUNGCHEONG": "충청", "YEONGNAM": "영남"}
STORES = {f"demo-store-{i:02d}": (f"시연 {i}호점", list(REGIONS)[(i - 1) // 3]) for i in range(1, 13)}

RECIPE = ("demo-cafe-recipes", "demo-cafe-recipes-1.0")
HYGIENE = ("food-sanitation-act", "food-sanitation-act-2026-10-08")
HYGIENE_RULE = ("food-sanitation-act-rule", "food-sanitation-act-rule-2026-09-01")
CONSUMER = ("consumer-dispute-standard-annex", "consumer-dispute-standard-annex-2025-12-18")
DISCLOSURE = ("paik-franchise-disclosure", "paik-franchise-disclosure-2025-11-21")
FRANCHISE_ACT = ("franchise-act", "franchise-act-2026-10-02")

# topic: (category, questions, status weights {answered, needs_review, handoff}, source, location)
TOPICS = {
    "vanilla_syrup": ("recipe", [
        "아이스 바닐라라테 시럽 몇 mL 넣어요?", "바닐라라테 시럽 펌프 수가 헷갈려요", "따뜻한 바닐라라테도 시럽 양 같아요?",
        "바닐라라테 덜 달게 해달라는데 시럽 얼마나 빼요?", "바닐라라테 손님이 너무 달다고 하는데 기준이 몇 mL예요?",
        "바닐라 시럽 20 mL 맞아요? 매장마다 다르게 넣는 것 같아요",
    ], {"answered": 85, "needs_review": 15, "handoff": 0}, RECIPE, "PDF 5쪽"),
    "espresso_shot": ("recipe", [
        "아이스 아메리카노 샷 몇 개예요?", "샷 추출 시간 기준이 있나요?", "라테는 샷 몇 개 넣어요?",
    ], {"answered": 95, "needs_review": 5, "handoff": 0}, RECIPE, "PDF 3쪽"),
    "recipe_steps": ("recipe", [
        "딸기 넣었어 다음에 뭐 넣어야 돼?", "아이스 라테 우유랑 샷 중 뭘 먼저 넣어요?", "모카는 소스 먼저 넣어요?",
        "말차 파우더 우유에 바로 넣어도 돼요?",
    ], {"answered": 90, "needs_review": 10, "handoff": 0}, RECIPE, "PDF 8쪽"),
    "menu_option": ("recipe", [
        "얼음 적게 요청하면 우유 더 채워요?", "사이즈업하면 시럽도 늘려요?", "디카페인 변경 가능한가요?",
    ], {"answered": 70, "needs_review": 30, "handoff": 0}, RECIPE, "PDF 2쪽"),
    "new_menu": ("recipe", [
        "신메뉴 딸기라테 따뜻하게도 돼요?", "신메뉴 레시피 언제부터 적용이에요?", "신메뉴 교육 자료 어디 있어요?",
    ], {"answered": 55, "needs_review": 45, "handoff": 0}, RECIPE, "PDF 8쪽"),
    "hygiene_storage": ("hygiene", [
        "우유 개봉 후 며칠까지 써도 돼요?", "냉장고 온도 몇 도로 맞춰야 해요?", "유통기한 지난 시럽 써도 되나요?",
        "딸기 베이스 냉동 보관해도 돼요?", "식재료 보관 기록 꼭 남겨야 해요?",
    ], {"answered": 60, "needs_review": 30, "handoff": 10}, HYGIENE_RULE, "PDF 41쪽"),
    "hygiene_education": ("hygiene", [
        "위생교육 아르바이트생도 받아야 해요?", "위생교육 매년 받아야 하나요?", "영업 전에 위생교육 먼저 받아야 해요?",
    ], {"answered": 85, "needs_review": 15, "handoff": 0}, HYGIENE, "PDF 30쪽 / 제41조"),
    "refund_exchange": ("consumer", [
        "음료에 이물질 나왔다고 환불해 달래요", "배달 음료 쏟아졌다고 환불 요청 왔어요",
        "손님 [이름]님이 [전화번호]로 환불 문의했어요", "주문번호 [주문번호] 취소하고 환불해 달래요",
        "맛이 이상하다고 다시 만들어 달래요",
    ], {"answered": 20, "needs_review": 45, "handoff": 35}, CONSUMER, "PDF 122쪽"),
    "customer_complaint": ("consumer", [
        "직원 응대가 불친절하다고 항의 들어왔어요", "대기 시간 길다고 리뷰에 남겼어요", "음료가 너무 달다고 항의해요",
    ], {"answered": 25, "needs_review": 45, "handoff": 30}, CONSUMER, "PDF 120쪽"),
}
PROSPECT_TOPICS = {
    "franchise_cost": ("franchise", [
        "처음 내는 돈에 교육비랑 보증금도 들어가요?", "가맹비 얼마예요?", "인테리어 비용 본사가 일부 내주나요?",
        "로열티 매달 얼마 내요?",
    ], {"answered": 80, "needs_review": 20, "handoff": 0}, DISCLOSURE, "PDF 19쪽"),
    "franchise_opening": ("franchise", [
        "매장 자리는 제가 구해야 하나요?", "계약부터 오픈까지 얼마나 걸려요?", "정보공개서 받고 바로 계약해도 돼요?",
    ], {"answered": 75, "needs_review": 25, "handoff": 0}, FRANCHISE_ACT, "PDF 6쪽 / 제7조"),
    "franchise_training": ("franchise", [
        "오픈 교육 대신 다른 사람이 가도 돼요?", "신메뉴 교육 꼭 참석해야 해요?",
    ], {"answered": 80, "needs_review": 20, "handoff": 0}, DISCLOSURE, "PDF 51쪽"),
}

# (week offset, topic, region or None for any, count). Week -1 = 지난 완료 주(기본 화면), -2 = 비교 기간, 0 = 진행 중.
PLAN = [
    (-2, "vanilla_syrup", None, 6), (-2, "espresso_shot", None, 8), (-2, "recipe_steps", None, 10),
    (-2, "menu_option", None, 7), (-2, "hygiene_storage", None, 6), (-2, "hygiene_education", None, 5),
    (-2, "refund_exchange", None, 7), (-2, "customer_complaint", None, 5),
    (-2, "franchise_cost", None, 9), (-2, "franchise_opening", None, 5), (-2, "franchise_training", None, 3),
    # 지난주: 서울 바닐라라테 시럽 문의 급증, 영남 식재료 보관 문의 증가
    (-1, "vanilla_syrup", "SEOUL", 18), (-1, "vanilla_syrup", None, 6), (-1, "espresso_shot", None, 8),
    (-1, "recipe_steps", None, 11), (-1, "menu_option", None, 7), (-1, "new_menu", None, 4),
    (-1, "hygiene_storage", "YEONGNAM", 10), (-1, "hygiene_storage", None, 4), (-1, "hygiene_education", None, 5),
    (-1, "refund_exchange", None, 9), (-1, "customer_complaint", None, 6),
    (-1, "franchise_cost", None, 10), (-1, "franchise_opening", None, 6), (-1, "franchise_training", None, 3),
    # 이번 주(진행 중)
    (0, "vanilla_syrup", None, 4), (0, "recipe_steps", None, 6), (0, "new_menu", None, 6),
    (0, "hygiene_storage", None, 3), (0, "refund_exchange", None, 4), (0, "franchise_cost", None, 4),
    (0, "menu_option", None, 3), (0, "espresso_shot", None, 3),
]
PENDING = [(-2, 2), (-1, 3), (0, 1)]       # 분류 대기(주제 없음)
SYSTEM_ERRORS = [(-2, 1), (-1, 2)]          # 모델 장애로 실패한 질문

def q(s):
    return "'" + s.replace("'", "''") + "'"

def ts(week, minute):
    if week == 0:  # 진행 중인 주: 월요일 0시부터 지금까지 사이에 비율로 퍼뜨린다(미래 시각 없음)
        frac = round(random.uniform(0.02, 0.98), 3)
        return f"@demo_monday_utc + INTERVAL FLOOR({frac} * @demo_elapsed_min) MINUTE"
    return f"@demo_monday_utc + INTERVAL {week * 7 * 1440 + minute} MINUTE"

def work_minute():
    day = random.randint(0, 6)
    return day * 1440 + random.randint(9 * 60, 21 * 60)  # KST 09~21시

rows, sources = [], []
n = 0

def add(week, topic, region, status, question, category, src=None, loc=None, error=None):
    global n
    n += 1
    qid = f"q-demo-seed-{n:03d}"
    prospect = category == "franchise"
    if prospect:
        user, role, store, sname, rcode, rname = "user-demo-prospect-01", "prospect", None, None, None, None
    else:
        choices = [s for s, (_, r) in STORES.items() if region is None or r == region]
        store = random.choice(choices)
        sname, rcode = STORES[store]
        rname = REGIONS[rcode]
        user, role = "user-demo-" + store[-2:], "owner"
    resolution = "unknown"
    if status in ("needs_review", "handoff"):
        resolution = random.choices(["open", "in_progress", "closed"], [50, 25, 25])[0]
    cls = "pending" if topic is None else "classified"
    when = ts(week, work_minute())
    vals = [q(qid), q(f"req-demo-seed-{n:03d}"), q(f"conv-demo-seed-{n:03d}"), q("brand-demo-a"), q(user), q(role),
            q(store) if store else "NULL", q(sname) if sname else "NULL", q(rcode) if rcode else "NULL",
            q(rname) if rname else "NULL", when, when, q(question), q(status), q(resolution), q(category),
            q(topic) if topic else "NULL", q(cls), q("demo-seed"), "NULL", q(error) if error else "NULL",
            str(random.randint(1800, 9500)) if status != "system_error" else "NULL"]
    rows.append("(" + ", ".join(vals) + ")")
    if src and status in ("answered", "needs_review"):
        sources.append(f"({q(qid)}, {q('chunk-demo-seed-' + str(n))}, {q(src[0])}, {q(src[1])}, {q(loc)})")

for week, topic, region, count in PLAN:
    category, questions, weights, src, loc = (TOPICS | PROSPECT_TOPICS)[topic]
    for _ in range(count):
        status = random.choices(list(weights), list(weights.values()))[0]
        add(week, topic, region, status, random.choice(questions), category, src, loc)
for week, count in PENDING:
    for _ in range(count):
        add(week, None, None, "needs_review", random.choice(["매장 와이파이 비밀번호 바꿔도 돼요?", "다음 주 알바 몇 명 배치해요?"]), "other")
for week, count in SYSTEM_ERRORS:
    for _ in range(count):
        add(week, "recipe_steps", None, "system_error", "아이스 라테 제조 순서 알려주세요", "recipe", error="UPSTREAM_TIMEOUT")

cols = ("question_id, request_id, conversation_id, brand_id, user_id, user_role, store_id, store_name, region_code, "
        "region_name, created_at, updated_at, question_redacted, answer_status, resolution_status, category, topic_id, "
        "classification_status, classification_version, result, error_code, latency_ms")
print(f"""-- 본사 대시보드 시연용 합성 질문 기록 {n}건 (실제 사용자 질문 아님, 마스킹된 문장).
-- db/demo 위치: 운영 스키마(db/migration)와 분리. 테스트 프로필은 읽지 않는다.
-- 시각은 적용 시점의 KST 이번 주 월요일 기준 상대값이라 '지난 완료 주'에 항상 데이터가 있다.
--   지지난주: 비교 기간 / 지난주: 서울 바닐라라테 시럽 문의 급증, 영남 식재료 보관 문의 증가 / 이번 주: 진행 중
-- Flyway 반복 마이그레이션(R__): 버전 번호를 쓰지 않아 이후 db/migration의 V4.. 추가와 순서가 꼬이지 않는다.
-- 내용이 바뀔 때만 다시 실행되며, 실행할 때마다 기존 합성 기록을 지우고 새로 넣는다.
-- ponytail: 적용 시점 기준으로 넣으므로 몇 주 지나면 '지난주'가 빈다. 시연 전에 볼륨을 새로 만들면 다시 채워진다.
-- 생성기: backend/scripts/gen_demo_question_events.py (seed 20261026). 수정은 생성기에서 하고 이 파일을 다시 만든다.

DELETE FROM question_sources WHERE question_id LIKE 'q-demo-seed-%';
DELETE FROM question_events WHERE question_id LIKE 'q-demo-seed-%';

SET @demo_kst_now = CONVERT_TZ(UTC_TIMESTAMP(6), '+00:00', '+09:00');
SET @demo_monday_utc = CONVERT_TZ(DATE(@demo_kst_now) - INTERVAL WEEKDAY(@demo_kst_now) DAY, '+09:00', '+00:00');
SET @demo_elapsed_min = TIMESTAMPDIFF(MINUTE, @demo_monday_utc, UTC_TIMESTAMP(6));

INSERT INTO question_events ({cols}) VALUES
""" + ",\n".join(rows) + ";\n\nINSERT INTO question_sources (question_id, chunk_id, document_id, version_id, page_or_article) VALUES\n"
      + ",\n".join(sources) + ";")
