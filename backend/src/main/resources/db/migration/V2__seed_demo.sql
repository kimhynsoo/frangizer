-- 합성 시연 데이터: 가상 1개 브랜드, 4권역, 12점포.
-- 비밀번호는 저장소에 두지 않는다. DEMO_PASSWORD 환경변수로 기동 시 설정된다.

INSERT INTO regions (code, name) VALUES
    ('SEOUL', '서울'), ('GYEONGGI', '경기'), ('CHUNGCHEONG', '충청'), ('YEONGNAM', '영남');

INSERT INTO stores (store_id, brand_id, name, region_code) VALUES
    ('demo-store-01', 'brand-demo-a', '시연 1호점', 'SEOUL'),
    ('demo-store-02', 'brand-demo-a', '시연 2호점', 'SEOUL'),
    ('demo-store-03', 'brand-demo-a', '시연 3호점', 'SEOUL'),
    ('demo-store-04', 'brand-demo-a', '시연 4호점', 'GYEONGGI'),
    ('demo-store-05', 'brand-demo-a', '시연 5호점', 'GYEONGGI'),
    ('demo-store-06', 'brand-demo-a', '시연 6호점', 'GYEONGGI'),
    ('demo-store-07', 'brand-demo-a', '시연 7호점', 'CHUNGCHEONG'),
    ('demo-store-08', 'brand-demo-a', '시연 8호점', 'CHUNGCHEONG'),
    ('demo-store-09', 'brand-demo-a', '시연 9호점', 'CHUNGCHEONG'),
    ('demo-store-10', 'brand-demo-a', '시연 10호점', 'YEONGNAM'),
    ('demo-store-11', 'brand-demo-a', '시연 11호점', 'YEONGNAM'),
    ('demo-store-12', 'brand-demo-a', '시연 12호점', 'YEONGNAM');

INSERT INTO users (id, username, role, brand_id, store_id, permissions) VALUES
    ('user-demo-01', 'demo-owner-a001', 'owner', 'brand-demo-a', 'demo-store-01', 'rag:ask'),
    ('user-demo-02', 'demo-owner-a002', 'owner', 'brand-demo-a', 'demo-store-02', 'rag:ask'),
    ('user-demo-03', 'demo-owner-a003', 'owner', 'brand-demo-a', 'demo-store-03', 'rag:ask'),
    ('user-demo-04', 'demo-owner-a004', 'owner', 'brand-demo-a', 'demo-store-04', 'rag:ask'),
    ('user-demo-05', 'demo-owner-a005', 'owner', 'brand-demo-a', 'demo-store-05', 'rag:ask'),
    ('user-demo-06', 'demo-owner-a006', 'owner', 'brand-demo-a', 'demo-store-06', 'rag:ask'),
    ('user-demo-07', 'demo-owner-a007', 'owner', 'brand-demo-a', 'demo-store-07', 'rag:ask'),
    ('user-demo-08', 'demo-owner-a008', 'owner', 'brand-demo-a', 'demo-store-08', 'rag:ask'),
    ('user-demo-09', 'demo-owner-a009', 'owner', 'brand-demo-a', 'demo-store-09', 'rag:ask'),
    ('user-demo-10', 'demo-owner-a010', 'owner', 'brand-demo-a', 'demo-store-10', 'rag:ask'),
    ('user-demo-11', 'demo-owner-a011', 'owner', 'brand-demo-a', 'demo-store-11', 'rag:ask'),
    ('user-demo-12', 'demo-owner-a012', 'owner', 'brand-demo-a', 'demo-store-12', 'rag:ask'),
    ('user-demo-prospect-01', 'demo-prospect-01', 'prospect', 'brand-demo-a', NULL, 'rag:ask'),
    ('user-demo-hq-01', 'demo-hq-01', 'hq', 'brand-demo-a', NULL,
     'rag:ask,document:write,document:approve,document:publish,analytics:read,analytics:write');

-- RAG 분류기(classify.py)와 같은 ID를 써야 한다.
INSERT INTO topics (id, label, category) VALUES
    ('vanilla_syrup', '바닐라라테 시럽 계량', 'recipe'),
    ('espresso_shot', '에스프레소 샷·추출', 'recipe'),
    ('recipe_steps', '메뉴 제조 순서', 'recipe'),
    ('menu_option', '옵션·사이즈 변경', 'recipe'),
    ('new_menu', '신메뉴', 'recipe'),
    ('hygiene_education', '위생교육', 'hygiene'),
    ('hygiene_storage', '식재료 보관·유통기한', 'hygiene'),
    ('refund_exchange', '교환·환급', 'consumer'),
    ('customer_complaint', '고객 불만 응대', 'consumer'),
    ('franchise_cost', '가맹점 부담 비용', 'franchise'),
    ('franchise_opening', '개점 절차·입지', 'franchise'),
    ('franchise_training', '가맹 교육', 'franchise');
