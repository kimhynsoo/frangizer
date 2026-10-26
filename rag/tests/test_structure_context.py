import asyncio
import hashlib
from dataclasses import replace

from app.retrieval.faiss_store import FaissRetriever
from app.schemas import InternalQuery
from app.structure import infer_hierarchy, search_text
from tests.support import fixture_evidence, query_payload


def row(cid, page, text, **changes):
    meta = fixture_evidence(cid, page=page, material_type='official', is_demo=False,
                            section_key=f'p{page}', **changes).metadata.model_dump(mode='json')
    meta['content_sha256'] = hashlib.sha256(text.encode()).hexdigest()
    return {'metadata': meta, 'text': text, 'section': {'parent_section_key': f'p{page}', 'part': 1}}


def test_continued_page_keeps_proven_heading_and_separates_midpage_scope():
    before = row('header', 10, '3. 식품소분업자와 판매업자의 준수사항\n가. 관련 기록을 남긴다.')
    text = '증거품은 해당 기간 동안 보관한다.\n4. 식품자동판매기영업자의 준수사항\n가. 기계를 관리한다.'
    after = row('continued', 11, text)
    result = infer_hierarchy([before, after])['continued']
    spans = result['segments']
    assert result['multiple_scopes'] and result['source_chunk_ids'] == ['header']
    assert spans[0]['headings'][-1]['text'].startswith('3.')
    assert spans[1]['headings'][-1]['text'].startswith('4.')
    assert '증거품' in text[spans[0]['start']:spans[0]['end']]
    assert '식품자동판매기' not in text[spans[0]['start']:spans[0]['end']]
    assert after['text'] == text
    assert '식품소분업자' in search_text(text, result)


def test_context_does_not_cross_versions_periods_or_unreviewed_table():
    header = row('header', 1, '2. 식료품(19개 업종)')
    other = row('other', 2, '이물혼입과 해결기준', version_id='other-version')
    assert infer_hierarchy([header, other])['other']['source_chunk_ids'] == []
    blocked = row('blocked', 2, '3. 의약품(2개 업종)')
    blocked['review'] = {'quality_flags': ['table_needs_review']}
    continued = row('tail', 3, '이물혼입과 해결기준')
    inferred = infer_hierarchy([header, blocked, continued])
    assert inferred['tail']['source_chunk_ids'] == []
    assert inferred['blocked']['segments'][0]['headings'] == []


def test_unauthorized_heading_is_filtered_before_context_recovery(tmp_path):
    header = fixture_evidence('header', page=1, section_key='p1', material_type='official', visibility='hq')
    header = replace(header, text='2. 내부 비공개 제품류', metadata=header.metadata.model_copy(update={
        'content_sha256': hashlib.sha256('2. 내부 비공개 제품류'.encode()).hexdigest()}))
    tail = fixture_evidence('tail', page=2, section_key='p2', material_type='official')
    retriever = FaissRetriever(tmp_path, None)
    retriever._load = lambda _: ([header, tail], [[1], [1]])
    items, _ = retriever._allowed(InternalQuery.model_validate(query_payload()))
    assert len(items) == 1 and items[0].metadata.chunk_id == 'tail'
    assert items[0].hierarchy['source_chunk_ids'] == []
    assert '비공개' not in items[0].search_text
    assert hashlib.sha256(items[0].text.encode()).hexdigest() == tail.metadata.content_sha256


def test_missing_page_breaks_inherited_scope():
    before = row('header', 1, '2. 식료품(19개 업종)')
    after = row('tail', 3, '다른 업종일 수 있는 이어진 표')
    assert infer_hierarchy([before, after])['tail']['source_chunk_ids'] == []


def test_wrapped_subject_qualification_is_not_lost_and_instruction_is_not_a_heading():
    title = '3. 식품소분·판매(자동판매기영업을 제외한\n다)·운반업자와 그 종업원의 준수사항'
    before = row('header', 1, title + '\n가. 기록을 남긴다.')
    tail = row('tail', 2, '1) 영업자 간 원재료를 공동 사용하지 말 것\n증거품 관련 규정')
    value = infer_hierarchy([before, tail])['tail']
    assert value['segments'][0]['headings'][-1]['text'] == title
    assert len(value['segments']) == 1
