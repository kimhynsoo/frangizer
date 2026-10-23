import os
from typing import Annotated, Protocol

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import tool
from pydantic import Field

from ..errors import upstream_error
from ..schemas import Category, ContractModel, Id, Status


class Draft(ContractModel):
    """Internal model output. URLs and source metadata are server-owned."""

    answer: Annotated[str, Field(min_length=1, max_length=6000)]
    steps: Annotated[list[Annotated[str, Field(min_length=1, max_length=300)]], Field(max_length=12)]
    status: Status
    used_chunk_ids: Annotated[list[Id], Field(max_length=20)]
    follow_up_questions: Annotated[list[Annotated[str, Field(min_length=1, max_length=200)]], Field(max_length=3)]
    handoff_reason: Annotated[str, Field(min_length=1, max_length=1000)] | None
    category: Category


@tool
def search_documents(query: str) -> str:
    """허용된 문서에서 질문에 필요한 근거를 검색한다. 적용 범위는 서버가 고정한다."""
    raise RuntimeError("Executed only by the graph's guarded retrieval adapter")


@tool
def read_sections(chunk_ids: list[str]) -> str:
    """이번 요청에서 검색된 조각의 상위 섹션·표 주석·조건·예외를 확인한다."""
    raise RuntimeError("Executed only by the graph's guarded retrieval adapter")


TOOLS = [search_documents, read_sections]


class ModelDriver(Protocol):
    name: str

    async def decide(self, messages: list[BaseMessage]) -> AIMessage: ...

    async def finalize(self, messages: list[BaseMessage]) -> tuple[Draft, AIMessage]: ...


class LangChainModel:
    """Works with a compatible BaseChatModel; no model state is shared across requests."""

    def __init__(self, model, name: str):
        self.name = name
        self.decider = model.bind_tools(TOOLS, strict=True)
        self.writer = model.with_structured_output(
            Draft.model_json_schema(), method="json_schema", strict=True, include_raw=True,
        )

    @classmethod
    def from_env(cls):
        # Called only by an integration factory after a real retriever is available.
        from langchain_openai import ChatOpenAI

        name, key = os.environ.get("LLM_MODEL"), os.environ.get("LLM_API_KEY")
        if not name or not key:
            raise ValueError("LLM_MODEL and LLM_API_KEY are required")
        return cls(ChatOpenAI(model=name, api_key=key, max_retries=0), name)

    async def decide(self, messages):
        result = await self.decider.ainvoke(messages)
        if not isinstance(result, AIMessage) or result.invalid_tool_calls:
            raise upstream_error()
        return result

    async def finalize(self, messages):
        result = await self.writer.ainvoke(messages)
        if result.get("parsing_error") or not isinstance(result.get("parsed"), dict):
            raise upstream_error()
        return Draft.model_validate(result["parsed"]), result["raw"]
