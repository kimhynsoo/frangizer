import asyncio
from app.retrieval.reranker import LLMReranker, selection_excerpt
from langchain_core.messages import AIMessage
from tests.support import fixture_evidence
import pytest


class Writer:
    def __init__(self, ids): self.ids = ids
    async def ainvoke(self, messages):
        return {'parsed': {'selected_ids': self.ids},
                'raw': AIMessage(content='', usage_metadata={'input_tokens': 11, 'output_tokens': 3, 'total_tokens': 14})}


def test_reranker_selects_only_existing_positions_and_accounts_for_usage():
    items = [fixture_evidence('a'), fixture_evidence('b')]
    result = asyncio.run(LLMReranker(Writer([1, 0]), 'offline').rank('질문', items))
    assert result.positions == (1, 0) and result.input_tokens == 11 and result.output_tokens == 3
    assert asyncio.run(LLMReranker(Writer([]), 'offline').rank('무관한 질문', items)).positions == ()


@pytest.mark.parametrize('ids', [[9], [1, 1], [-1]])
def test_reranker_rejects_invented_or_duplicate_positions(ids):
    with pytest.raises(ValueError, match='mapping'):
        asyncio.run(LLMReranker(Writer(ids), 'offline').rank('질문', [fixture_evidence('a'), fixture_evidence('b')]))


def test_ranking_excerpt_does_not_hide_a_relevant_tail():
    text = '문서 머리말\n' + '일반 안내입니다. ' * 300 + '\n교육비의 납부기한과 부가세 조건을 확인합니다.'
    excerpt = selection_excerpt(text, '교육비 납부기한 부가세')
    assert '문서 머리말' in excerpt and '교육비의 납부기한' in excerpt
    assert len(excerpt) <= 1800
