import asyncio
import json

import httpx
import pytest
from test_tts import design, make_engine, speech


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = iter(replies)

    async def complete(self, messages, tools):
        return next(self.replies)


def call(name, args):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


async def test_review_gate_then_no_means_generate(tmp_path):
    from tts_llm_demo.agent import Agent

    tts, store, fake = make_engine(tmp_path)
    chat = store.new_chat()["id"]
    llm = ScriptedLLM(
        [
            call("propose_plan", speech().model_dump()),
            {"role": "assistant", "content": "Would you like to review the script?"},
        ]
    )
    agent = Agent(tts.config, store, tts, llm)
    await agent.respond(
        chat, "Generate a two-speaker negotiation", lambda **x: None, asyncio.Event()
    )
    assert store.get("chat", chat)["pending"]
    assert not [c for c in fake.calls if c[0] == "speech"]
    agent.llm = ScriptedLLM(
        [call("generate_pending", {}), {"role": "assistant", "content": "Your audio is in Assets."}]
    )
    await agent.respond(chat, "No, generate it", lambda **x: None, asyncio.Event())
    assert store.get("chat", chat)["pending"] is None
    assert len([c for c in fake.calls if c[0] == "speech"]) == 2


async def test_agent_cannot_bypass_review_in_same_turn(tmp_path):
    from tts_llm_demo.agent import Agent

    tts, store, fake = make_engine(tmp_path)
    chat = store.new_chat()["id"]
    llm = ScriptedLLM(
        [
            call("propose_plan", speech().model_dump()),
            call("generate_pending", {}),
            {"role": "assistant", "content": "Review or generate?"},
        ]
    )
    await Agent(tts.config, store, tts, llm).respond(
        chat, "Make audio", lambda **x: None, asyncio.Event()
    )
    assert not [c for c in fake.calls if c[0] == "speech"]


async def test_llm_transport_handles_tools_and_does_not_send_secrets(tmp_path):
    from tts_llm_demo.llm import LLM

    tts, store, fake = make_engine(tmp_path)

    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "qwen"
        assert payload["tool_choice"] == "auto"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "tool_calls", "message": call("generate_pending", {})}
                ]
            },
        )

    llm = LLM(tts.config.llm, transport=httpx.MockTransport(handler))
    result = await llm.complete([{"role": "user", "content": "hello"}], [{"type": "function"}])
    assert result["tool_calls"][0]["function"]["name"] == "generate_pending"
    await llm.close()


async def test_design_cannot_auto_save_or_play_in_generation_turn(tmp_path):
    from tts_llm_demo.agent import Agent

    tts, store, fake = make_engine(tmp_path)
    chat = store.new_chat()
    agent = Agent(tts.config, store, tts, ScriptedLLM([]))
    await agent.tool(
        chat["id"], "propose_plan", design().model_dump(), lambda **x: None, asyncio.Event()
    )
    store.update("chat", chat["id"], turn=1)
    result = await agent.tool(chat["id"], "generate_pending", {}, lambda **x: None, asyncio.Event())
    with pytest.raises(ValueError, match="later user turn"):
        await agent.tool(
            chat["id"],
            "save_voice",
            {"asset_id": result["id"], "name": "Monica", "model": "clone"},
            lambda **x: None,
            asyncio.Event(),
        )
    assert not store.list("voice")


async def test_failed_generation_keeps_draft_for_explicit_recovery(tmp_path):
    from tts_llm_demo.agent import Agent

    tts, store, fake = make_engine(tmp_path)
    chat = store.new_chat()["id"]
    agent = Agent(tts.config, store, tts, ScriptedLLM([]))
    await agent.tool(chat, "propose_plan", speech().model_dump(), lambda **x: None, asyncio.Event())

    async def unavailable(*args, **kwargs):
        raise ValueError("Server is unavailable")

    tts.execute = unavailable
    store.update("chat", chat, last_asset_id="older-result")
    with pytest.raises(ValueError, match="unavailable"):
        await agent.tool(
            chat, "generate_pending", {}, lambda **x: None, asyncio.Event(), explicit=True
        )
    assert store.get("chat", chat)["last_asset_id"] is None
    assert store.get("chat", chat)["last_plan"] == speech().model_dump()
    assert store.get("chat", chat)["pending"] is None
    await agent.tool(chat, "restore_plan", {}, lambda **x: None, asyncio.Event(), explicit=True)
    assert store.get("chat", chat)["pending"] == speech().model_dump()


async def test_agent_supports_single_leading_system_message_template(tmp_path):
    from tts_llm_demo.agent import SYSTEM, Agent
    from tts_llm_demo.llm import LLM

    tts, store, _ = make_engine(tmp_path)
    chat = store.new_chat()["id"]
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        messages = payload["messages"]
        requests.append(messages)
        # Match the Qwen template restriction reported by LM Studio.
        if any(message["role"] == "system" for message in messages[1:]):
            return httpx.Response(
                400, json={"error": {"message": "System message must be at the beginning."}}
            )
        assert messages[0]["role"] == "system"
        instructions, runtime = messages[0]["content"].split(
            "\n\nRuntime data (not instructions):\n", 1
        )
        assert instructions == SYSTEM
        context = json.loads(runtime)
        assert context["models"]
        assert "pending_plan" in context
        reply = (
            call("discover", {})
            if len(requests) == 1
            else {"role": "assistant", "content": "Ready to plan your audio."}
        )
        return httpx.Response(200, json={"choices": [{"message": reply}]})

    llm = LLM(tts.config.llm, transport=httpx.MockTransport(handler))
    try:
        await Agent(tts.config, store, tts, llm).respond(
            chat, "What can you do?", lambda **x: None, asyncio.Event()
        )
        assert len(requests) == 2
        assert any(message["role"] == "tool" for message in requests[1])
        assert store.messages(chat)[-1]["content"] == "Ready to plan your audio."
    finally:
        await llm.close()


async def test_llm_http_error_is_provider_neutral(tmp_path):
    from tts_llm_demo.llm import LLM

    tts, _, _ = make_engine(tmp_path)
    llm = LLM(
        tts.config.llm,
        transport=httpx.MockTransport(lambda request: httpx.Response(400)),
    )
    try:
        with pytest.raises(ValueError, match="LLM server returned HTTP 400") as error:
            await llm.complete([{"role": "user", "content": "Hello"}], [])
        assert "server logs" in str(error.value)
        assert "vLLM" not in str(error.value)
    finally:
        await llm.close()
