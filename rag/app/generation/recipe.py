"""Conservative structured recipe shortcut, grounded in the gateway's approved PDF revision.

Fully recognized lookups use stored values; unresolved requests use the counseling agent.
Draft JSON is not an authority: source hash and every returned field must match indexed text.
"""
import json
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path

from ..schemas import Usage
from ..retrieval.base import Evidence
from .answer import assemble_answer
from .model import Draft, CounselDraft
from .review import AnswerBlock, Quote, Quantity, source_units, reference_key
from .evidence_policy import table_blocked


def compact(text):
    return re.sub(r'\s+', '', text).lower().replace('라떼', '라테')


def is_catalog_request(question):
    text = compact(question).rstrip('?!？!.,。')
    return bool(re.fullmatch(
        r'(?:(?:조회|호출|확인|사용)가능한(?:레시피|메뉴)(?:목록)?'
        r'|(?:등록된)?(?:레시피|메뉴)목록'
        r'|어떤(?:레시피|메뉴)(?:가)?있(?:어|어요|나요))'
        r'(?:을|를)?(?:알려줘|알려주세요|보여줘|보여주세요)?', text))


@dataclass(frozen=True)
class RecipeRequest:
    menu_id: str
    temperatures: tuple[str, ...]
    ingredients: tuple[str, ...]
    asks: frozenset[str]
    option: str | None
    servings: tuple[int, ...]
    completed_step: int | None


def parse_request(question, data):
    """A small positive grammar, not a blacklist of words that imply complexity.

    Every substantive span must be consumed. Unknown qualifiers, substitutions,
    comparisons and conversation references remain for the counseling planner.
    This parser never produces quantities or treats an unknown phrase as default.
    """
    rest = compact(question)

    def take(terms):
        nonlocal rest
        terms = sorted({compact(t) for t in terms if t}, key=len, reverse=True)
        if not terms:
            return False
        rest, count = re.subn('|'.join(map(re.escape, terms)), ' ', rest)
        return bool(count)

    menus = [m for m in data['menus'] if any(compact(n) in rest
             for n in [m['display_name'], *m.get('aliases', [])])]
    if len(menus) != 1:
        return None
    menu = menus[0]
    take([menu['display_name'], *menu.get('aliases', [])])
    records = [r for r in data['recipe_versions'] if r['menu_id'] == menu['menu_id']]
    temperatures = tuple(t for t, names in (
        ('HOT', ('뜨거운', 'hot', '핫')), ('ICED', ('차가운', '아이스', 'iced', 'ice')),
    ) if take(names))
    option_names = {o['name'] for r in records for o in r.get('order_rules', {}).get('options', [])}
    options = []
    for name in sorted(option_names, key=len, reverse=True):
        aliases = [name]
        if compact(name) == '덜달게':
            aliases += ['달지 않게', '덜 달게']
        aliases += [a + ' 하면' for a in aliases]
        if take(aliases):
            options.append(name)
    if len(options) > 1:
        return None
    # Ingredient vocabulary comes from the catalog, including short names such as 시럽.
    ingredients = []
    names = {i['name'] for r in data['recipe_versions'] for i in r['ingredients']}
    for name in sorted(names, key=lambda n: len(compact(n)), reverse=True):
        if take([name]):
            ingredients.append(name)
    for term in sorted({n.split()[-1] for n in names}, key=len, reverse=True):
        if take([term]):
            ingredients.append(term)
    numbers = {'한': 1, '두': 2, '세': 3, '네': 4, '다섯': 5, '여섯': 6,
               '일곱': 7, '여덟': 8, '아홉': 9, '열': 10}
    counts = []

    def count(match):
        value = match[1]
        counts.append(int(value) if value.isdigit() else numbers[value])
        return ' '

    rest = re.sub(r'(\d+|다섯|여섯|일곱|여덟|아홉|한|두|세|네|열)잔', count, rest)
    done = re.search(r'(\d+)단계(?:까지)?(?:완료|했어|했어요|했음)', rest)
    completed = int(done[1]) if done else None
    if done:
        rest = rest[:done.start()] + ' ' + rest[done.end():]
    asks = set()
    for intent, phrases in (
        ('recipe', ('레시피', '만드는법', '제조법', '어떻게만들어', '어떻게만들어요')),
        ('next', ('다음단계', '다음순서', '다음')),
        ('steps', ('제조 순서', '제조 단계', '단계', '순서')),
        ('pumps', ('펌프횟수', '펌프')),
        ('ingredients', ('재료',)),
        ('quantity', ('몇밀리리터', '몇밀리', '몇그램', '몇ml', '몇g', '얼만큼', '얼마나', '얼마', '계량', '양')),
    ):
        if take(phrases):
            asks.add(intent)
    if not asks:
        return None
    # Only connectors, question particles and presentation verbs can remain.
    # Do not add substantive words here: unsupported requests must not disappear.
    filler = ('알려주세요', '알려줘', '알려줄래', '알려줘요', '보여줘', '보여주세요',
              '넣어요', '넣어', '들어가요', '들어가', '필요해요', '필요해',
              '무엇인지', '확인',
              '어떻게', '예요', '이에요', '인가요', '이야', '뭐야', '뭔가요', '야',
              '주세요', '좀', '몇', '는', '은', '을', '를', '이', '가', '에', '로',
              '와', '과', '랑', '하고', '그리고', '까지', '도', '요', '의')
    take(filler)
    if re.sub(r'[\s?!？!,.·:;~～"“”\'()]', '', rest):
        return None
    return RecipeRequest(menu['menu_id'], temperatures, tuple(dict.fromkeys(ingredients)),
                         frozenset(asks), options[0] if options else None, tuple(counts), completed)


@dataclass(frozen=True)
class RecipeResult:
    text: str
    items: tuple[Evidence, ...] = ()
    steps: tuple[str, ...] = ()
    quantities: tuple[Quantity, ...] = ()
    follow: tuple[str, ...] = ()


def recipe_source(record, items):
    """Bind a record to its menu/version/temperature region, not a whole page.

    This validates the existing indexed source; it never extracts a new document
    or changes the data team's artifacts. Unknown layouts fail closed.
    """
    expected = '재료계량단위' + ''.join(i['name'] + i['amount'] + i['unit'] for i in record['ingredients'])
    expected += ''.join(f"{step['order']}.{step['text']}" for step in record['steps'])
    for item in items:
        if item.truncated or table_blocked(item):
            continue
        for unit in source_units(item):
            text = unit['text']
            sections = list(re.finditer(r'(?m)^\s*(HOT|ICED|주문·제조 확인사항|이 메뉴의 판본 이력)\s*$', text))
            header = text[:sections[0].start()] if sections else ''
            menu = re.search(r'메뉴\s*ID\s+(\w+)', header)
            edition = re.search(r'판본\s*([^/\n]+)', header)
            versions = re.findall(r'v(\d+(?:\.\d+)+)', edition[1]) if edition else []
            if (not menu or menu[1] != record['menu_id'] or item.metadata.version not in versions
                    or (len(set(versions)) > 1 and '=' not in edition[1])
                    or compact(record['serving_basis']) not in compact(header)):
                continue
            for n, section in enumerate(sections):
                if section[1] != record['temperature']:
                    continue
                body = text[section.end():sections[n + 1].start() if n + 1 < len(sections) else len(text)]
                if compact(body) != compact(expected):
                    continue
                notes = re.search(r'주문·제조 확인사항\s*([\s\S]*?)(?:이 메뉴의 판본 이력|$)', text)
                return Quote(chunk_id=item.metadata.chunk_id, unit_id=unit['unit_id']), compact(notes[1]) if notes else ''
    return None


class RecipeShortcut:
    def __init__(self, path, retriever):
        self.data = json.loads(Path(path).read_text())
        if self.data.get('schema') != 'frangizer.recipe-structured.draft' or self.data.get('schema_version') != 0:
            raise ValueError('Unsupported structured recipe format')
        self.retriever = retriever

    async def resolve_item(self, context, item, evidence):
        """Both callers use the same executor; no text round-trip or number parsing."""
        if item.lookup is None:
            return None
        lookup = item.lookup
        request = RecipeRequest(lookup.menu_id, (lookup.temperature,) if lookup.temperature else (),
            tuple(lookup.ingredients), frozenset((lookup.operation,)), lookup.option, (lookup.servings,), None)
        result = await self.execute(context, request)
        if result is None or result.follow or result.steps:
            return None
        allowed = {reference_key(r) for r in item.evidence_refs}
        refs = []
        for q in result.quantities:
            if q.evidence_ref not in refs:
                refs.append(q.evidence_ref)
        if (not refs or any(reference_key(r) not in allowed or r.chunk_id not in evidence for r in refs)
                or any((evidence[e.metadata.chunk_id].text, evidence[e.metadata.chunk_id].metadata) != (e.text, e.metadata)
                       for e in result.items if e.metadata.chunk_id in evidence)):
            return None
        return CounselDraft(category='recipe', blocks=[AnswerBlock(block_id=0, item_id=item.item_id,
            text=result.text, placement='answer', evidence_refs=refs, quantities=list(result.quantities))])

    async def try_answer(self, context):
        catalog = is_catalog_request(context.question)
        request = parse_request(context.question, self.data)
        # A bare temperature selects the latest explicit recipe request only.
        # Re-read authorized sources below; previous answers are never recipe data.
        if request is None and context.history:
            choice = re.fullmatch(r'(핫|hot|아이스|iced|ice)(?:으로|로)?(?:이요|요|해주세요|주세요)?[.!?。？！]*',
                                  compact(context.question))
            if choice:
                previous = parse_request(context.history[-1].question, self.data)
                if previous is not None and not previous.temperatures:
                    temperature = 'HOT' if choice[1] in ('핫', 'hot') else 'ICED'
                    request = replace(previous, temperatures=(temperature,))
        if request is None and not catalog:
            return None
        started = time.monotonic()
        result = await self.catalog(context) if catalog else await self.execute(context, request)
        if result is None:
            return None
        draft = Draft(answer=result.text, steps=list(result.steps), status='needs_review' if result.follow else 'answered',
            used_chunk_ids=[e.metadata.chunk_id for e in result.items], follow_up_questions=list(result.follow),
            handoff_reason=None, category='recipe')
        return assemble_answer(context, draft, {e.metadata.chunk_id: e for e in result.items},
            latency_ms=int((time.monotonic() - started) * 1000), candidate_count=len(result.items),
            usage=Usage(input_tokens=0, output_tokens=0, embedding_tokens=0, model='not-used',
                        embedding_model='not-used', estimated_cost_usd=None), llm_called=False,
            no_evidence_action='clarify' if result.follow and not result.items else None)

    async def _available_records(self, context):
        candidates = await self.retriever.recipe_candidates(context, self.data['doc_id'])
        recipes = {}
        for record in self.data['recipe_versions']:
            for source in record['sources']:
                for sha, items in candidates:
                    documents = [d for d in self.data['source_documents'] if d['version'] == source['document_version'] and d['sha256'] == sha]
                    page_items = tuple(e for e in items if e.metadata.page == source['page'] and e.metadata.version == source['document_version'])
                    if documents and page_items:
                        recipes[record['recipe_version_id']] = (record, page_items)
        return recipes

    async def catalog(self, context):
        if context.principal.role not in ('owner', 'hq'):
            return None
        available = await self._available_records(context)
        menus, evidence = {}, {}
        names = {m['menu_id']: m['display_name'] for m in self.data['menus']}
        for record, items in available.values():
            if record['menu_id'] not in names or recipe_source(record, items) is None:
                continue
            menus.setdefault(record['menu_id'], set()).add(record['temperature'])
            evidence.update((e.metadata.chunk_id, e) for e in items)
        if not menus:
            notice = '현재 조회 가능한 레시피가 없습니다. 배포된 레시피 문서와 접근 권한을 확인해 주세요.'
            return RecipeResult(notice, follow=(notice,))
        lines = [f"- {names[mid]}: {' · '.join(t for t in ('HOT', 'ICED') if t in temperatures)}"
                 for mid, temperatures in menus.items()]
        return RecipeResult('현재 조회 가능한 레시피입니다.\n' + '\n'.join(lines)
                            + '\n메뉴명과 온도를 함께 입력해 주세요.', tuple(evidence.values()))

    async def execute(self, context, request):
        """Authorized stored values and provenance; public rendering belongs to callers."""
        if context.principal.role not in ('owner', 'hq'):
            return None
        menu = next((m for m in self.data['menus'] if m['menu_id'] == request.menu_id), None)
        if menu is None or not request.asks or request.asks - {'quantity', 'ingredients', 'pumps', 'recipe', 'steps', 'next'}:
            return None
        sequence = bool(request.asks & {'recipe', 'steps', 'next'})
        if request.option and sequence:
            return None
        if any(n != 1 for n in request.servings):
            return RecipeResult('여러 잔의 제조량은 저장된 한 잔 기준으로 임의 환산하지 않습니다.',
                                follow=('한 잔 기준 레시피를 확인할까요?',))
        if len(request.temperatures) > 1:
            return RecipeResult('한 번에 확인할 메뉴 온도를 선택해 주세요.', follow=('HOT과 ICED 중 어느 레시피인가요?',))
        recipes = {key: (record, items) for key, (record, items) in (await self._available_records(context)).items()
                   if record['menu_id'] == request.menu_id
                   and (not request.temperatures or record['temperature'] in request.temperatures)}
        if not recipes:
            return RecipeResult('현재 허용된 판본에서 해당 메뉴의 적용 레시피를 확인할 수 없습니다.',
                                follow=('메뉴명과 적용 판본을 확인해 주세요.',))

        def quantities(ingredients):
            return ', '.join(f"{i['name']} {i['amount']} {i['unit']}" for i in ingredients)

        def render(record, items):
            origin = recipe_source(record, items)
            if origin is None:
                return None
            ref, notes = origin
            values = []

            def measure(label, value, unit):
                q = Quantity(label=label, value=value, unit=unit, evidence_ref=ref)
                if q not in values:
                    values.append(q)

            def measured(ingredients):
                for i in ingredients:
                    measure(i['name'], i['amount'], i['unit'])
                return quantities(ingredients)

            def noted(rule):
                text = compact(rule['text'])
                versions = re.findall(r'v(\d+\.\d+)', rule['text'])
                return bool(text and text in notes and all(v == items[0].metadata.version for v in versions))

            ingredients = [dict(i) for i in record['ingredients']]
            option = None
            if request.option:
                options = [o for o in record.get('order_rules', {}).get('options', []) if o['name'] == request.option]
                if len(options) != 1:
                    return RecipeResult('저장된 판본에서 해당 주문 옵션을 확인할 수 없습니다.', follow=('옵션명을 확인해 주세요.',))
                option = options[0]
                setting, text = option['set'], compact(option['text'])
                targets = [i for i in ingredients if i['name'] == setting['ingredient']]
                if (len(targets) != 1 or not noted(option) or compact(setting['amount'] + setting['unit']) not in text
                        or compact(setting['ingredient'].split()[-1]) not in text):
                    return None
                targets[0].update(amount=setting['amount'], unit=setting['unit'])
            terms = request.ingredients or ((option['set']['ingredient'],) if option else ())
            if not terms and 'pumps' in request.asks:
                names = {p['ingredient'] for p in record.get('order_rules', {}).get('pumps', [])}
                if len(names) == 1:
                    terms = tuple(names)
            selected = [i for i in ingredients if any(compact(t) in compact(i['name']) for t in terms)]
            if terms and any(not any(compact(t) in compact(i['name']) for i in ingredients) for t in terms):
                return RecipeResult('해당 재료를 이 레시피에서 확인할 수 없습니다.', follow=('확인하려는 재료명을 알려 주세요.',))
            if not selected and not sequence:
                selected = ingredients if not terms and 'ingredients' in request.asks else []
                if not selected:
                    return RecipeResult('확인하려는 재료를 특정할 수 없습니다.', follow=('확인하려는 재료명을 알려 주세요.',))
            if 'next' in request.asks and 'quantity' in request.asks and not selected:
                return RecipeResult('계량값을 확인할 재료가 필요합니다.', follow=('확인하려는 재료명을 알려 주세요.',))
            parts, steps, follow = [], (), ()
            if sequence:
                ordered = sorted(record['steps'], key=lambda s: s['order'])
                if 'next' in request.asks:
                    done = request.completed_step
                    if done is None or done not in {s['order'] for s in ordered}:
                        return RecipeResult('완료한 단계를 확인해야 다음 순서를 안내할 수 있습니다.', follow=('몇 단계까지 완료했나요?',))
                    ordered = [s for s in ordered if s['order'] == done + 1]
                    if ordered:
                        measure('다음 단계', str(ordered[0]['order']), '')
                    parts.append(f"다음은 {ordered[0]['order']}단계입니다." if ordered else '저장된 마지막 단계까지 완료했습니다.')
                else:
                    parts.append('재료: ' + measured(ingredients))
                steps = tuple(s['text'] for s in ordered)
            if request.asks & {'quantity', 'ingredients'} and (not sequence or 'next' in request.asks):
                parts.append(measured(ingredients if 'ingredients' in request.asks else selected))
            if 'pumps' in request.asks:
                missing = not selected
                for ingredient in selected:
                    rules = ([dict(option['set'], text=option['text'])] if option and option['set']['ingredient'] == ingredient['name'] else
                             record.get('order_rules', {}).get('pumps', []))
                    rules = [p for p in rules if p['ingredient'] == ingredient['name'] and p['amount'] == ingredient['amount'] and p['unit'] == ingredient['unit']]
                    rule = rules[0] if len(rules) == 1 else None
                    if (not rule or not rule.get('pumps') or not noted(rule)
                            or compact(rule['amount'] + rule['unit']) not in compact(rule['text'])
                            or compact('펌프' + rule['pumps'] + '회') not in compact(rule['text'])):
                        missing = True
                        continue
                    measure(ingredient['name'] + ' 전용 펌프', rule['pumps'], '회')
                    measured([ingredient])
                    parts.append(f"{ingredient['name']} 전용 펌프 {rule['pumps']}회 ({rule['amount']} {rule['unit']})")
                if missing:
                    parts.append('펌프 환산 기준을 문서에서 확인할 수 없습니다. 계량값: ' + measured(selected))
                    follow = ('사용 중인 펌프 규격과 재료명을 확인해 주세요.',)
            basis = re.fullmatch(r'(\d+)잔', record['serving_basis'])
            if not basis:
                return None
            measure('제조 기준', basis[1], '잔')
            return RecipeResult('\n'.join(parts), items, steps, tuple(values), follow)

        rendered = [(r, items, render(r, items)) for r, items in recipes.values()]
        if any(value is None for _, _, value in rendered):
            return None
        if len(rendered) != 1:
            identities = {(e.metadata.document_id, e.metadata.version_id, e.metadata.index_revision) for _, items, _ in rendered for e in items}
            first = rendered[0][2]
            if (sequence or len(rendered) != 2 or len(identities) != 1
                    or {r['temperature'] for r, _, _ in rendered} != {'HOT', 'ICED'}
                    or len({r['serving_basis'] for r, _, _ in rendered}) != 1
                    or any((v.text, v.steps, v.follow) != (first.text, first.steps, first.follow) for _, _, v in rendered)):
                return RecipeResult('온도에 따라 제조 기준이 달라질 수 있습니다.', follow=('HOT과 ICED 중 어느 레시피인가요?',))
            temperature = 'HOT·ICED 공통'
        else:
            temperature = rendered[0][0]['temperature']
        record, _, first = rendered[0]
        evidence = {e.metadata.chunk_id: e for _, items, _ in rendered for e in items}
        label = f"{menu['display_name']} {temperature} · {record['serving_basis']}"
        if request.option:
            label += ' · ' + request.option
        return RecipeResult(label + ': ' + first.text, tuple(evidence.values()), first.steps, first.quantities, first.follow)


class RagRuntime:
    def __init__(self, counselor, recipe):
        self.counselor, self.recipe = counselor, recipe
        self.retriever = counselor.retriever

    async def run(self, context):
        answer = await self.recipe.try_answer(context)
        return answer if answer is not None else await self.counselor.run(context)
