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
        assert state["chats"][0]["last_asset_id"] == asset["id"]
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


def test_delete_chat_preserves_audio_and_settings_survive_restart(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        chat = client.post("/api/chats", headers=HEADERS).json()
        other = client.post("/api/chats", headers=HEADERS).json()
        store = app.state.store
        store.message(chat["id"], "user", "Remove this history")
        store.message(other["id"], "user", "Keep this history")
        store.put("asset", {"id": "a" * 32, "chat_id": chat["id"]})
        store.audio_path("a" * 32).write_bytes(b"keep audio")
        assert client.delete(f"/api/chats/{chat['id']}").status_code == 403
        assert client.delete(f"/api/chats/{chat['id']}", headers=HEADERS).status_code == 200
        state = client.get("/api/state").json()
        assert [c["id"] for c in state["chats"]] == [other["id"]]
        assert [m["content"] for m in state["messages"]] == ["Keep this history"]
        assert client.get("/api/assets/" + "a" * 32 + "/audio").content == b"keep audio"
        assert client.delete(f"/api/chats/{chat['id']}", headers=HEADERS).status_code == 404
        assert state["settings"]["delete_clips"] is False
        assert (
            client.put("/api/settings", headers=HEADERS, json={"delete_clips": True}).status_code
            == 200
        )
    with TestClient(make_app(tmp_path)) as client:
        assert client.get("/api/state").json()["settings"]["delete_clips"] is True


def test_delete_and_settings_blocked_during_generation(tmp_path):
    import asyncio

    app = make_app(tmp_path)
    with TestClient(app) as client:
        chat = client.post("/api/chats", headers=HEADERS).json()
        app.state.store.update("chat", chat["id"], pending=speech().model_dump())

        async def blocked(**kwargs):
            await asyncio.Event().wait()

        app.state.tts.client.generate_speech = blocked
        response = client.post(
            f"/api/chats/{chat['id']}/actions", headers=HEADERS, json={"action": "generate"}
        )
        assert response.status_code == 202
        asset = seed_asset(app.state.store, "f")
        assert client.delete(f"/api/assets/{asset}", headers=HEADERS).status_code == 409
        assert (
            client.post(
                "/api/assets/delete", headers=HEADERS, json={"asset_ids": [asset]}
            ).status_code
            == 409
        )
        assert client.delete(f"/api/chats/{chat['id']}", headers=HEADERS).status_code == 409
        assert (
            client.put("/api/settings", headers=HEADERS, json={"delete_clips": True}).status_code
            == 409
        )
        run_id = response.json()["id"]
        client.post(f"/api/runs/{run_id}/cancel", headers=HEADERS)
        assert wait_run(client, run_id)["status"] == "cancelled"


def seed_asset(store, digit, **fields):
    asset = store.put("asset", {"id": digit * 32, "kind": "clip", **fields})
    store.audio_path(asset["id"]).write_bytes(b"audio")
    return asset["id"]


def test_bulk_delete_removes_files_and_references_only_for_selected_assets(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        store = app.state.store
        clip = seed_asset(store, "a")
        combined = seed_asset(store, "b", kind="conversation", clips=[clip])
        chat = store.new_chat()
        store.update("chat", chat["id"], last_asset_id=combined)
        store.put("playback", {"id": "event", "asset_id": clip, "chat_id": chat["id"]})
        response = client.post("/api/assets/delete", headers=HEADERS, json={"asset_ids": [clip]})
        assert response.status_code == 200
        assert response.json() == {"deleted": [clip], "failed": []}
        assert not store.audio_path(clip).exists()
        assert store.get("asset", combined)["clips"] == []
        assert not store.list("playback")
        assert client.get(f"/api/assets/{clip}/audio").status_code == 404
        assert client.get(f"/api/assets/{combined}/audio").status_code == 200
        assert client.delete(f"/api/assets/{combined}", headers=HEADERS).status_code == 200
        assert store.get("chat", chat["id"])["last_asset_id"] is None
    with TestClient(make_app(tmp_path)) as client:
        assert not client.get("/api/state").json()["assets"]


def test_delete_batch_preflight_protects_saved_voices_and_unknown_ids(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        store = app.state.store
        ordinary = seed_asset(store, "a")
        reference = seed_asset(store, "b", kind="voice_preview")
        store.put("voice", {"id": "voice", "asset_id": reference})
        for ids, status in [([ordinary, reference], 409), ([ordinary, "c" * 32], 404), ([], 422)]:
            assert (
                client.post(
                    "/api/assets/delete", headers=HEADERS, json={"asset_ids": ids}
                ).status_code
                == status
            )
            assert len(store.list("asset")) == 2
            assert store.audio_path(ordinary).exists()
        assert client.delete(f"/api/assets/{ordinary}").status_code == 403


def test_delete_reports_locked_file_without_removing_its_record(tmp_path, monkeypatch):
    from pathlib import Path

    app = make_app(tmp_path)
    with TestClient(app) as client:
        store = app.state.store
        locked, free = seed_asset(store, "a"), seed_asset(store, "b")
        unlink = Path.unlink

        def guarded(path, **kwargs):
            if path.name == locked + ".wav":
                raise PermissionError("file is in use")
            return unlink(path, **kwargs)

        monkeypatch.setattr(Path, "unlink", guarded)
        response = client.post(
            "/api/assets/delete", headers=HEADERS, json={"asset_ids": [locked, free, free]}
        )
        assert response.json() == {"deleted": [free], "failed": [locked]}
        assert store.get("asset", locked)
        assert store.audio_path(locked).exists()
        assert not store.audio_path(free).exists()
