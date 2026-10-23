"""Conservative structured recipe shortcut, grounded in the gateway's approved PDF revision.

Unsupported/composite requests go to the counseling agent. No new endpoint or public schema.
Draft JSON is not an authority: source hash and every returned field must match indexed text.
"""
import json
import re
import time
from pathlib import Path

from ..schemas import Usage
from .answer import assemble_answer
from .model import Draft


def compact(text):
    return re.sub(r'\s+', '', text).lower().replace('라떼', '라테')


class RecipeShortcut:
    def __init__(self, path, retriever):
        self.data = json.loads(Path(path).read_text())
        if self.data.get('schema') != 'frangizer.recipe-structured.draft' or self.data.get('schema_version') != 0:
            raise ValueError('Unsupported structured recipe format')
        self.retriever = retriever

    async def try_answer(self, context):
        if context.principal.role not in ('owner', 'hq'):
            return None
        question = compact(context.question)
        menus = [m for m in self.data['menus'] if any(compact(name) in question for name in
                 [m['display_name'], *m.get('aliases', [])])]
        # Conservative recognition; comparative/modified recipes require the general agent.
        if len(menus) != 1 or any(x in question for x in (
            '변경', '비교', '대신', '빼고', '추가', '펌프', '두잔', '2잔', '적게', '많이', '없이', '두배', '반만',
        )):
            return None
        quantity = any(x in question for x in ('얼마', '몇ml', '몇g', '양', '재료', '얼만큼'))
        sequence = any(x in question for x in ('단계', '순서', '다음', '레시피', '만드는법', '제조법'))
        if not quantity and not sequence:
            return None
        started = time.monotonic()
        menu = menus[0]
        candidates = await self.retriever.recipe_candidates(context, self.data['doc_id'])
        recipes = []
        for record in self.data['recipe_versions']:
            # Publication/effective dates are owned by Java and the indexed chunks.
            # Planned JSON dates must not retire a still-published previous version.
            if record['menu_id'] != menu['menu_id']:
                continue
            for source in record['sources']:
                for sha, items in candidates:
                    documents = [d for d in self.data['source_documents'] if d['version'] == source['document_version']
                                 and d['sha256'] == sha]
                    page_items = tuple(e for e in items if e.metadata.page == source['page']
                                       and e.metadata.version == source['document_version'])
                    if documents and page_items:
                        recipes.append((record, page_items))
        hot = any(word in question for word in ('핫', 'hot', '뜨거운'))
        iced = any(word in question for word in ('아이스', 'iced', 'ice', '차가운'))
        if hot != iced:
            recipes = [r for r in recipes if r[0]['temperature'] == ('HOT' if hot else 'ICED')]
        distinct = {r[0]['recipe_version_id']: r for r in recipes}
        recipes = list(distinct.values())

        def grounded(record, items):
            body = compact('\n'.join(e.text for e in items))
            fields = [i['name'] + i['amount'] + i['unit'] for i in record['ingredients']]
            fields += [s['text'] for s in record['steps']]
            return all(compact(field) in body for field in fields)

        def requested_ingredients(record):
            ingredient_terms = ('시럽', '우유', '얼음', '에스프레소', '소스', '퓨레', '파우더', '온수', '정수')
            mentioned = [term for term in ingredient_terms if compact(term) in question]
            if mentioned:
                return [i for i in record['ingredients'] if any(compact(term) in compact(i['name']) for term in mentioned)]
            return record['ingredients'] if '재료' in question else []

        def quantities(ingredients):
            return ', '.join(f"{i['name']} {i['amount']} {i['unit']}" for i in ingredients)

        def result(answer, *, items=(), steps=(), follow=(), status='answered'):
            draft = Draft(answer=answer, steps=list(steps), status=status,
                          used_chunk_ids=[e.metadata.chunk_id for e in items],
                          follow_up_questions=list(follow), handoff_reason=None, category='recipe')
            return assemble_answer(context, draft, {e.metadata.chunk_id: e for e in items},
                latency_ms=int((time.monotonic() - started) * 1000), candidate_count=len(items),
                usage=Usage(input_tokens=0, output_tokens=0, embedding_tokens=0, model='not-used',
                            embedding_model='not-used', estimated_cost_usd=None), llm_called=False)

        if not recipes:
            return result('현재 허용된 판본에서 해당 메뉴의 적용 레시피를 확인할 수 없습니다.',
                          follow=('메뉴명과 적용 판본을 확인해 주세요.',), status='needs_review')
        if len(recipes) != 1:
            # Do not ask for temperature when the requested values are identical.
            if (quantity and not sequence and len(recipes) == 2
                    and {r['temperature'] for r, _ in recipes} == {'HOT', 'ICED'}
                    and len({r['serving_basis'] for r, _ in recipes}) == 1):
                values = [[(i['name'], i['amount'], i['unit']) for i in requested_ingredients(r)] for r, _ in recipes]
                identities = {(e.metadata.document_id, e.metadata.version_id, e.metadata.index_revision)
                              for _, items in recipes for e in items}
                if (values[0] and values[0] == values[1] and len(identities) == 1
                        and all(grounded(r, items) for r, items in recipes)):
                    combined = {e.metadata.chunk_id: e for _, items in recipes for e in items}
                    return result(f"{menu['display_name']} HOT·ICED 공통 · {recipes[0][0]['serving_basis']}: "
                                  + quantities(requested_ingredients(recipes[0][0])), items=tuple(combined.values()))
            return result('온도에 따라 제조 기준이 달라질 수 있습니다.',
                          follow=('HOT과 ICED 중 어느 레시피인가요?',), status='needs_review')
        record, items = recipes[0]
        # Verify structured values against actual indexed text, including draft JSON.
        if not grounded(record, items):
            return None
        label = f"{menu['display_name']} {record['temperature']} · {record['serving_basis']}"
        if sequence:
            next_step = re.search(r'(\d+)단계.*(?:완료|했|다음)', question)
            if '다음' in question and next_step is None:
                return result('완료한 단계를 확인해야 다음 순서를 정확히 안내할 수 있습니다.',
                              items=items, follow=('몇 단계까지 완료했나요?',), status='needs_review')
            steps = sorted(record['steps'], key=lambda s: s['order'])
            if next_step:
                done = int(next_step.group(1))
                if done not in {s['order'] for s in steps}:
                    return result('완료한 단계 번호를 확인해 주세요.', items=items,
                                  follow=('레시피의 몇 단계까지 완료했나요?',), status='needs_review')
                steps = [s for s in steps if s['order'] == done + 1]
                if not steps:
                    return result(label + ': 저장된 마지막 단계까지 완료했습니다.', items=items)
            return result(label, items=items, steps=[f"{s['order']}. {s['text']}" for s in steps])
        ingredients = requested_ingredients(record)
        if not ingredients:
            return result('해당 재료를 이 레시피에서 확인할 수 없습니다.', items=items,
                          follow=('확인하려는 재료명을 알려 주세요.',), status='needs_review')
        return result(label + ': ' + quantities(ingredients), items=items)


class RagRuntime:
    def __init__(self, counselor, recipe):
        self.counselor, self.recipe = counselor, recipe
        self.retriever = counselor.retriever

    async def run(self, context):
        answer = await self.recipe.try_answer(context)
        return answer if answer is not None else await self.counselor.run(context)
