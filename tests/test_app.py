import time

import pytest
from fastapi.testclient import TestClient
from test_agent import ScriptedLLM, call
from test_tts import FakeTTS, speech

from tts_llm_demo.config import Config, LLMConfig


def make_app(tmp_path, replies=None):
    from tts_llm_demo.app import create_app

    config = Config(llm=LLMConfig(base_url="http://llm/v1", model="qwen"), storage_dir=tmp_path)
    return create_app(config, tts_client=FakeTTS(), llm=ScriptedLLM(replies or []))


def wait_run(client, run_id):
    for _ in range(100):
        run = next(r for r in client.get("/api/state").json()["runs"] if r["id"] == run_id)
        if run["status"] not in {"running", "cancelling"}:
            return run
        time.sleep(0.02)
    pytest.fail("run did not finish")


HEADERS = {"X-Demo-Request": "1"}


def test_http_review_generate_download_and_restart(tmp_path):
    app = make_app(
        tmp_path,
        [
            call("propose_plan", speech().model_dump()),
            {"role": "assistant", "content": "Review first or generate now?"},
        ],
    )
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        chat = client.post("/api/chats", headers=HEADERS).json()
        response = client.post(
            f"/api/chats/{chat['id']}/messages",
            headers=HEADERS,
            json={"text": "Generate a negotiation"},
        )
        assert response.status_code == 202
        assert wait_run(client, response.json()["id"])["status"] == "succeeded"
        state = client.get("/api/state").json()
        assert state["chats"][0]["pending"] and not state["assets"]
        response = client.post(
            f"/api/chats/{chat['id']}/actions", headers=HEADERS, json={"action": "generate"}
        )
        assert wait_run(client, response.json()["id"])["status"] == "succeeded"
        state = client.get("/api/state").json()
        asset = state["assets"][-1]
        assert client.get(f"/api/assets/{asset['id']}/audio").content[:4] == b"RIFF"
        assert "secret" not in state
        assert (
            client.post(
                f"/api/chats/{chat['id']}/actions", headers=HEADERS, json={"action": "generate"}
            ).status_code
            == 409
        )
    with TestClient(make_app(tmp_path)) as client:
        assert len(client.get("/api/state").json()["assets"]) == 3


def test_origin_header_and_unknown_paths(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        assert client.post("/api/chats").status_code == 403
        assert (
            client.post("/api/chats", headers={**HEADERS, "Origin": "https://evil.com"}).status_code
            == 403
        )
        assert client.get("/api/assets/not-a-real-id/audio").status_code == 404
        assert client.get("/api/state", headers={"Host": "evil.com"}).status_code == 400
