import asyncio
import hashlib
from dataclasses import replace

import pytest

from app.retrieval.base import parent_identity, reference_targets
from app.retrieval.faiss_store import FaissRetriever
from app.schemas import InternalQuery
from tests.support import fixture_evidence, query_payload


def article(cid, title, number, text, **changes):
    item = fixture_evidence(cid, title=title, article=f'제{number}조', section_key=f'art-{number}', **changes)
    return replace(item, text=text, metadata=item.metadata.model_copy(update={
        'content_sha256': hashlib.sha256(text.encode()).hexdigest()}))


def test_parent_expansion_cannot_mix_periods_quality_or_other_sections(tmp_path):
    anchor = replace(fixture_evidence('a', section_key='art-1.p1'), parent_section_key='art-1')
    sibling = replace(fixture_evidence('b', section_key='art-1.p2'), parent_section_key='art-1', section_part=2)
    blocked = replace(sibling, metadata=sibling.metadata.model_copy(update={'chunk_id': 'blocked'}),
                      quality_flags=('table_needs_review',))
    other = replace(fixture_evidence('other', section_key='art-2'), parent_section_key='art-2')
    # All rows can be in the permitted scope but must not be expanded across different effective periods.
    period = replace(sibling, metadata=sibling.metadata.model_copy(update={
        'chunk_id': 'period', 'effective_from': sibling.metadata.effective_from.replace(day=2)}))
    retriever = FaissRetriever(tmp_path, None)
    retriever._allowed = lambda _: ([anchor, sibling, blocked, other, period], [])
    batch = asyncio.run(retriever.read_sections(('a',), InternalQuery.model_validate(query_payload())))
    assert [e.metadata.chunk_id for e in batch.items] == ['a', 'b']
    assert all(parent_identity(e) == parent_identity(anchor) for e in batch.items)
    assert batch.notes  # A blocked sibling is omitted explicitly, never silently treated as complete.
    second = asyncio.run(retriever.read_sections(('b',), InternalQuery.model_validate(query_payload())))
    assert [e.metadata.chunk_id for e in second.items] == ['a', 'b']


def test_context_read_follows_explicit_references_once_within_authorized_versions(tmp_path):
    anchor = article('anchor', '테스트법 시행규칙', 1, '제1조 법 제2조 및 「다른법」 제9조의 기준을 확인한다.')
    law = article('law', '테스트법', 2, '제2조 원문의 적용 대상. 제3조도 참조한다.')
    external = article('external', '다른법', 9, '제9조 관련 기준을 정한다.')
    unrelated = article('unrelated', '무관한법', 2, '같은 조 번호여도 다른 법이다.')
    transitive = article('transitive', '테스트법', 3, '자동으로 다음 참조까지 확장하지 않는다.')
    forbidden = article('forbidden', '다른법', 9, '비공개 조항', visibility='hq')
    old = article('old', '다른법', 9, '옛 판본', version_id='unapproved')
    retriever = FaissRetriever(tmp_path, None)
    retriever._load = lambda _: ([anchor, law, external, unrelated, transitive, forbidden, old], [[1]] * 7)
    batch = asyncio.run(retriever.read_sections(('anchor',), InternalQuery.model_validate(query_payload())))
    assert {e.metadata.chunk_id for e in batch.items} == {'anchor', 'law', 'external'}


def test_missing_reference_does_not_expand_scope_or_claim_a_rule(tmp_path):
    anchor = article('anchor', '테스트법', 1, '제1조 「없는법」 제7조를 참조한다.')
    same_number = article('other', '다른법', 7, '다른 문서의 제7조다.')
    retriever = FaissRetriever(tmp_path, None)
    retriever._load = lambda _: ([anchor, same_number], [[1], [1]])
    batch = asyncio.run(retriever.read_sections(('anchor',), InternalQuery.model_validate(query_payload())))
    assert [e.metadata.chunk_id for e in batch.items] == ['anchor']
    assert any('없는법 제7조' in note for note in batch.notes)


@pytest.mark.parametrize(('text', 'expected'), [
    ('「다른법」 제2조 및 같은 법 제3조를 따른다.', (('다른법', '제2조'), ('다른법', '제3조'))),
    ('법 제2조 및 같은 법 제3조를 따른다.', (('테스트법', '제2조'), ('테스트법', '제3조'))),
    ('「다른법」에 따른다. 같은 법 제3조를 확인한다.', (('다른법', '제3조'),)),
    ('같은 법 제3조를 확인한다.', ()),
    ('「다른법」 제2조.\n\n같은 법 제3조를 확인한다.', (('다른법', '제2조'),)),
    ('「다른법」 제2조 및 같은 규칙 제3조.', (('다른법', '제2조'),)),
    ('「다른법 시행규칙」 제2조 및 같은 규칙 제3조.',
     (('다른법 시행규칙', '제2조'), ('다른법 시행규칙', '제3조'))),
    ('「다른법」 제2조, 외부법 제7조 및 같은 법 제8조.', (('다른법', '제2조'),)),
])
def test_demonstrative_references_require_an_explicit_matching_antecedent(text, expected):
    assert reference_targets(article('a', '테스트법 시행규칙', 1, text)) == expected


def test_demonstrative_context_read_does_not_substitute_current_law(tmp_path):
    anchor = article('a', '테스트법', 1, '「다른법」 제2조 및 같은 법 제3조를 따른다.')
    first = article('b', '다른법', 2, '외부 조항의 조건.')
    second = article('c', '다른법', 3, '외부 조항의 예외.')
    wrong = article('wrong', '테스트법', 3, '다른 법의 같은 조 번호.')
    retriever = FaissRetriever(tmp_path, None)
    retriever._load = lambda _: ([anchor, first, second, wrong], [[1]] * 4)
    batch = asyncio.run(retriever.read_sections(('a',), InternalQuery.model_validate(query_payload())))
    assert {e.metadata.chunk_id for e in batch.items} == {'a', 'b', 'c'}


def test_requested_sections_obey_size_limit_and_keep_request_priority(tmp_path):
    anchors = [article(cid, '테스트법', n, '본문 ' * 1700) for n, cid in enumerate(('a', 'b', 'c'), 1)]
    omitted_sibling = replace(article('sibling', '테스트법', 1, '생략한 조각의 문맥.'), section_part=2)
    retriever = FaissRetriever(tmp_path, None)
    retriever._allowed = lambda _: ([*anchors, omitted_sibling], [])
    batch = asyncio.run(retriever.read_sections(('c', 'b', 'a'), InternalQuery.model_validate(query_payload())))
    assert {e.metadata.chunk_id for e in batch.items} == {'b', 'c'}
    assert sum(len(e.text) for e in batch.items) <= 12000
    assert any('미확인 조각: a' in note for note in batch.notes)
    assert all(hashlib.sha256(e.text.encode()).hexdigest() == e.metadata.content_sha256 for e in batch.items)


def test_context_read_connects_explicit_delegated_definition_but_not_arbitrary_reverse_citations(tmp_path):
    anchor = article('anchor', '테스트법', 9, '제9조 대통령령으로 정하는 지정업자는 사전 심사를 받는다.')
    definition = article('definition', '테스트법 시행령', 17,
        '제17조 법 제9조에서 “대통령령으로 정하는 지정업자”란 등록된 업자를 말한다.')
    mention = article('mention', '테스트법 시행령', 18, '제18조 법 제9조에 따른 서류 제출 절차를 정한다.')
    wrong_term = article('wrong-term', '테스트법 시행령', 19,
        '제19조 법 제9조에서 “대통령령으로 정하는 시설”이란 신고된 시설을 말한다.')
    other_family = article('foreign', '다른법 시행령', 17,
        '제17조 「테스트법」 제9조에서 “대통령령으로 정하는 지정업자”란 다른 업자를 말한다.')
    transitive = article('transitive', '테스트법 시행규칙', 22,
        '제22조 영 제17조에서 “총리령으로 정하는 서류”란 지정 서식을 말한다.')
    forbidden = article('forbidden', '테스트법 시행령', 17, definition.text, visibility='hq')
    unapproved = article('old', '테스트법 시행령', 17, definition.text, version_id='unapproved')
    rows = [anchor, definition, mention, wrong_term, other_family, transitive, forbidden, unapproved]
    retriever = FaissRetriever(tmp_path, None)
    retriever._load = lambda _: (rows, [[1]] * len(rows))
    batch = asyncio.run(retriever.read_sections(('anchor',), InternalQuery.model_validate(query_payload())))
    assert {e.metadata.chunk_id for e in batch.items} == {'anchor', 'definition'}


def test_each_delegated_phrase_uses_its_own_child_document_kind():
    from app.retrieval.base import delegation_definition
    anchor = article('a', '테스트법', 11,
        '제11조 대통령령으로 정하는 업자와 총리령으로 정하는 심사 항목을 확인한다.')
    decree = article('d', '테스트법 시행령', 3,
        '제3조 법 제11조에서 “대통령령으로 정하는 업자”란 지정 업자를 말한다.')
    rule = article('r', '테스트법 시행규칙', 4,
        '제4조 법 제11조에서 “총리령으로 정하는 심사 항목”이란 공개된 항목을 말한다.')
    wrong_kind = article('w', '테스트법 시행령', 5, rule.text)
    assert delegation_definition(anchor, decree)
    assert delegation_definition(anchor, rule)
    assert not delegation_definition(anchor, wrong_kind)
