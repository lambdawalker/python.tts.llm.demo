import asyncio

import httpx
import pytest
from test_agent import ScriptedLLM, call
from test_tts import make_engine, speech

from tts_llm_demo.agent import Agent
from tts_llm_demo.llm import LLM, final_text
from tts_llm_demo.store import Store, uid


async def test_cancel_running_tts_requests_remote_cancellation(tmp_path):
    tts, store, fake = make_engine(tmp_path)
    entered = asyncio.Event()

    async def blocked(job_id):
        entered.set()
        await asyncio.Event().wait()

    fake.get_job = blocked
    task = asyncio.create_task(
        tts.execute(speech(), store.new_chat()["id"], lambda **x: None, asyncio.Event())
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ("cancel", "0") in fake.calls
    assert not store.list("asset")


def test_restart_marks_active_run_interrupted(tmp_path):
    store = Store(tmp_path)
    run_id = uid()
    store.put("run", {"id": run_id, "status": "running"})
    store.close()
    reopened = Store(tmp_path)
    assert reopened.get("run", run_id)["status"] == "interrupted"
    reopened.close()


async def test_bad_tool_arguments_cannot_generate(tmp_path):
    tts, store, fake = make_engine(tmp_path)
    malformed = call("generate_pending", {})
    malformed["tool_calls"][0]["function"]["arguments"] = "{bad"
    llm = ScriptedLLM([malformed, {"role": "assistant", "content": "Please review first."}])
    await Agent(tts.config, store, tts, llm).respond(
        store.new_chat()["id"], "hello", lambda **x: None, asyncio.Event()
    )
    assert not fake.calls


async def test_truncated_llm_tools_are_not_executed(tmp_path):
    tts, store, fake = make_engine(tmp_path)
    llm = LLM(
        tts.config.llm,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "length", "message": call("generate_pending", {})}
                    ]
                },
            )
        ),
    )
    with pytest.raises(ValueError, match="truncated"):
        await llm.complete([], [])
    await llm.close()
    assert final_text("<think>private thoughts</think>Hello") == "Hello"
    assert final_text("<think>unfinished thoughts") == ""


async def test_session_reset_only_removes_credentials(tmp_path):
    tts, store, fake = make_engine(tmp_path)
    chat = store.new_chat()
    store.put("secret", {"id": "tts_session", "access_token": "revoked"})
    await tts.reset_session()
    with pytest.raises(KeyError):
        store.get("secret", "tts_session")
    assert store.get("chat", chat["id"]) == chat
