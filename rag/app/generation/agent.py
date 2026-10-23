import asyncio
import json
import time
from pathlib import Path
from typing import TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from ..errors import ServiceError, unavailable, upstream_error
from ..privacy import redact
from ..retrieval.base import Evidence, Retriever, validate_batch
from ..schemas import InternalAnswer, InternalQuery, Usage
from .answer import assemble_answer
from .model import Draft, ModelDriver

PROMPT = Path(__file__).resolve().parents[2] / "prompts" / "counsel.md"


class AgentState(TypedDict):
    context: InternalQuery
    messages: list[BaseMessage]
    evidence: dict[str, Evidence]
    model_calls: int
    tool_calls: int
    search_calls: int
    candidate_count: int
    input_tokens: int
    output_tokens: int
    embedding_tokens: int
    embedding_model: str
    next_node: str
    draft: Draft | None


def tokens(message: AIMessage) -> dict:
    usage = message.usage_metadata or {}
    return {"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)}


class CounselAgent:
    def __init__(self, model: ModelDriver, retriever: Retriever, *, max_model_calls=4, max_tool_calls=3, max_search_calls=2):
        if not (2 <= max_model_calls <= 10 and 1 <= max_tool_calls <= 10 and 1 <= max_search_calls <= max_tool_calls):
            raise ValueError("invalid agent budget")
        self.model, self.retriever = model, retriever
        self.max_model_calls, self.max_tool_calls, self.max_search_calls = max_model_calls, max_tool_calls, max_search_calls
        self.prompt = PROMPT.read_text()
        graph = StateGraph(AgentState)
        graph.add_node("decide", self._decide)
        graph.add_node("tools", self._tools)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(START, "decide")
        graph.add_conditional_edges("decide", lambda s: s["next_node"], {"tools": "tools", "finalize": "finalize"})
        graph.add_edge("tools", "decide")
        graph.add_edge("finalize", END)
        self.graph = graph.compile()

    async def _decide(self, s: AgentState):
        # Reserve one model call for structured final output; never depend on the LLM to stop.
        if s["model_calls"] >= self.max_model_calls - 1 or s["tool_calls"] >= self.max_tool_calls:
            return {"next_node": "finalize"}
        message = await self.model.decide(s["messages"])
        if not isinstance(message, AIMessage) or message.invalid_tool_calls or len(message.tool_calls) > 8:
            raise upstream_error()
        usage = tokens(message)
        return {
            "messages": [*s["messages"], message], "model_calls": s["model_calls"] + 1,
            "input_tokens": s["input_tokens"] + usage["input_tokens"],
            "output_tokens": s["output_tokens"] + usage["output_tokens"],
            "next_node": "tools" if message.tool_calls else "finalize",
        }

    async def _tools(self, s: AgentState):
        messages = list(s["messages"])
        evidence = dict(s["evidence"])
        tool_calls, search_calls = s["tool_calls"], s["search_calls"]
        candidate_count, embedding_tokens = s["candidate_count"], s["embedding_tokens"]
        embedding_model = s["embedding_model"]
        for call in s["messages"][-1].tool_calls:
            if tool_calls >= self.max_tool_calls:
                messages.append(ToolMessage(content='{"error":"tool_budget_exhausted"}', tool_call_id=call["id"]))
                continue
            tool_calls += 1
            name, args = call["name"], call["args"]
            if name == "search_documents":
                if set(args) != {"query"} or not isinstance(args["query"], str) or not 1 <= len(args["query"].strip()) <= 2000:
                    raise upstream_error()
                if search_calls >= self.max_search_calls:
                    messages.append(ToolMessage(content='{"error":"search_budget_exhausted"}', tool_call_id=call["id"]))
                    continue
                search_calls += 1
                batch = await self.retriever.search(args["query"], s["context"])
            elif name == "read_sections":
                ids = args.get("chunk_ids")
                if (set(args) != {"chunk_ids"} or not isinstance(ids, list) or not 1 <= len(ids) <= 5
                        or any(not isinstance(i, str) or i not in evidence for i in ids)):
                    raise upstream_error()
                batch = await self.retriever.read_sections(tuple(ids), s["context"])
                # Do not let a loader return unrelated documents or sections.
                permitted_sections = {(evidence[i].metadata.document_id, evidence[i].metadata.version_id,
                                       evidence[i].metadata.index_revision, evidence[i].metadata.section_key) for i in ids}
                if any((e.metadata.document_id, e.metadata.version_id, e.metadata.index_revision, e.metadata.section_key)
                       not in permitted_sections for e in batch.items):
                    raise upstream_error()
            else:
                raise upstream_error()
            batch = validate_batch(batch, s["context"])
            source_models = {e.metadata.embedding_model for e in batch.items}
            if len(source_models) > 1:
                raise upstream_error()
            batch_model = batch.embedding_model if batch.embedding_model != "not-used" else next(iter(source_models), "not-used")
            if batch_model != "not-used":
                if embedding_model not in ("not-used", batch_model):
                    raise upstream_error()
                embedding_model = batch_model
                if any(e.metadata.embedding_model != embedding_model for e in batch.items):
                    raise upstream_error()
            candidate_count += len(batch.items)
            embedding_tokens += batch.embedding_tokens
            returned = []
            for item in batch.items:
                chunk_id = item.metadata.chunk_id
                if chunk_id in evidence and evidence[chunk_id] != item:
                    raise upstream_error()
                evidence[chunk_id] = item
                returned.append({
                    "chunk_id": chunk_id, "text": redact(item.text), "truncated": item.truncated,
                    "title": item.metadata.title, "page": item.metadata.page,
                    "article": item.metadata.article, "section_key": item.metadata.section_key,
                    "version": item.metadata.version, "material_type": item.metadata.material_type,
                    "is_demo": item.metadata.is_demo,
                    "legal_temporal_unreviewed": item.legal_temporal_unreviewed,
                    "effective_from": item.metadata.effective_from.isoformat(),
                    "effective_until": item.metadata.effective_until.isoformat() if item.metadata.effective_until else None,
                })
            messages.append(ToolMessage(content=json.dumps({"evidence": returned}, ensure_ascii=False), tool_call_id=call["id"]))
        return {
            "messages": messages, "evidence": evidence, "tool_calls": tool_calls, "search_calls": search_calls,
            "candidate_count": candidate_count, "embedding_tokens": embedding_tokens, "embedding_model": embedding_model,
        }

    async def _finalize(self, s: AgentState):
        messages = [*s["messages"], HumanMessage(content=(
            "이제 도구 사용을 종료하고 최종 초안을 작성하세요. 확보한 근거 조각 ID만 인용하세요. "
            "정보·근거 부족과 담당자 판단 필요를 구분하고, 없는 조건·정책을 만들지 마세요."
        ))]
        draft, raw = await self.model.finalize(messages)
        usage = tokens(raw)
        return {
            "draft": Draft.model_validate(draft), "model_calls": s["model_calls"] + 1,
            "input_tokens": s["input_tokens"] + usage["input_tokens"],
            "output_tokens": s["output_tokens"] + usage["output_tokens"],
        }

    async def run(self, context: InternalQuery) -> InternalAnswer:
        if not self.retriever.ready:
            raise unavailable()
        started = time.monotonic()
        messages: list[BaseMessage] = [SystemMessage(content=self.prompt), SystemMessage(content=(
            f"서버가 확정한 적용 기준은 {context.as_of.isoformat()}, basis={context.basis}입니다. "
            "미래 기준은 예정 기준임을 표시하고 현재 기준으로 바꾸지 마세요."
        ))]
        for turn in context.history:
            messages.extend([HumanMessage(content=turn.question), AIMessage(content=turn.answer)])
        messages.append(HumanMessage(content=context.question))
        state: AgentState = {
            "context": context, "messages": messages, "evidence": {}, "model_calls": 0,
            "tool_calls": 0, "search_calls": 0, "candidate_count": 0, "input_tokens": 0,
            "output_tokens": 0, "embedding_tokens": 0, "embedding_model": "not-used",
            "next_node": "decide", "draft": None,
        }
        try:
            async with asyncio.timeout(context.deadline_ms / 1000):
                # Never enable remote prompt/document tracing through inherited environment variables.
                with tracing_context(enabled=False):
                    state = await self.graph.ainvoke(state, {"recursion_limit": 30})
                return assemble_answer(
                    context, state["draft"], state["evidence"],
                    latency_ms=int((time.monotonic() - started) * 1000),
                    candidate_count=state["candidate_count"],
                    usage=Usage(
                        input_tokens=state["input_tokens"], output_tokens=state["output_tokens"],
                        embedding_tokens=state["embedding_tokens"], model=self.model.name,
                        embedding_model=state["embedding_model"], estimated_cost_usd=None,
                    ),
                )
        except TimeoutError:
            raise ServiceError(504, "UPSTREAM_TIMEOUT", "상담 처리 제한 시간을 초과했습니다.", retryable=True) from None
        except ServiceError:
            raise
        except Exception:
            # Provider exceptions may contain document text, credentials or raw prompts.
            raise upstream_error() from None
