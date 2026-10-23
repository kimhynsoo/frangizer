import asyncio
import json

import httpx
from langchain_openai import ChatOpenAI

from app.generation.agent import CounselAgent
from app.generation.model import LangChainModel
from app.schemas import InternalQuery
from .support import FixtureRetriever, draft, query_payload


def test_langchain_tool_call_and_structured_output_without_network():
    """Exercise real LangChain serialization/parsing against an in-memory HTTP transport."""
    async def run():
        calls = []

        def respond(request):
            payload = json.loads(request.content)
            calls.append(payload)
            if payload.get("response_format"):
                assert payload["response_format"]["type"] == "json_schema"
                assert payload["response_format"]["json_schema"]["strict"]
                message = {"role": "assistant", "content": draft().model_dump_json()}
                reason = "stop"
            elif any(m["role"] == "tool" for m in payload["messages"]):
                message = {"role": "assistant", "content": "근거 확인 완료"}
                reason = "stop"
            else:
                assert payload["tools"][0]["function"]["strict"]
                assert set(t["function"]["name"] for t in payload["tools"]) == {"search_documents", "read_sections"}
                message = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call-offline-1", "type": "function",
                    "function": {"name": "search_documents", "arguments": '{"query":"테스트 항목"}'},
                }]}
                reason = "tool_calls"
            return httpx.Response(200, json={
                "id": "chatcmpl-offline", "object": "chat.completion", "created": 1,
                "model": "offline-model", "choices": [{"index": 0, "message": message, "finish_reason": reason}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            chat = ChatOpenAI(model="offline-model", api_key="offline-test-only", http_async_client=client, max_retries=0)
            agent = CounselAgent(LangChainModel(chat, "offline-model"), FixtureRetriever())
            result = await agent.run(InternalQuery.model_validate(query_payload()))
            assert result.result.status == "answered"
            assert result.usage.input_tokens == 36 and result.usage.output_tokens == 24
            assert len(calls) == 3
    asyncio.run(run())
