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
    with pytest.raises(ValueError, match="unavailable"):
        await agent.tool(
            chat, "generate_pending", {}, lambda **x: None, asyncio.Event(), explicit=True
        )
    assert store.get("chat", chat)["last_plan"] == speech().model_dump()
    assert store.get("chat", chat)["pending"] is None
    await agent.tool(chat, "restore_plan", {}, lambda **x: None, asyncio.Event(), explicit=True)
    assert store.get("chat", chat)["pending"] == speech().model_dump()
