import asyncio
from types import SimpleNamespace as Obj

import numpy as np
import pytest
import soundfile as sf

from tts_llm_demo.config import Config, LLMConfig
from tts_llm_demo.schemas import Plan
from tts_llm_demo.store import Store


class Record(dict):
    def model_dump(self, **kwargs):
        return dict(self)

    def __getattr__(self, name):
        return self[name]


class FakeTTS:
    def __init__(self, design=True, cloning=True):
        self.features = {
            "talk": {"tts": True},
            "design": {"voice_design": design},
            "clone": {"tts": True, "voice_cloning": cloning},
        }
        self.calls = []
        self.jobs = {}

    async def list_models(self):
        return [Record(id=m) for m in self.features]

    async def capabilities(self, model):
        return Record(
            model=model,
            availability="ready",
            features={k: {"supported": v} for k, v in self.features[model].items()},
            controls={"instructions": {"supported": False}},
            languages=["en"],
            output={"formats": ["wav"], "sample_rates": [24000]},
            voice_required=True,
            references={"max_duration_seconds": 60},
            limits={"text_characters": 10000},
        )

    async def guidance(self, feature, model):
        return Record(model=model, feature=feature, revision="1", summary="Use plain text")

    async def list_voices(self, model):
        return (
            [Record(id="a", alias="Alice"), Record(id="b", alias="Bob")] if model == "talk" else []
        )

    async def validate_speech(self, **kwargs):
        self.calls.append(("validate", kwargs))
        return Record(valid=True)

    async def generate_speech(self, **kwargs):
        self.calls.append(("speech", kwargs))
        identifier = str(len(self.jobs))
        self.jobs[identifier] = kwargs
        return Obj(id=identifier)

    async def design_voice(self, **kwargs):
        self.calls.append(("design", kwargs))
        self.jobs["design"] = kwargs
        return Obj(id="design")

    async def get_job(self, job_id):
        return Obj(
            id=job_id,
            status="succeeded",
            stage="complete",
            results=[Obj(asset_id=job_id)],
            error=None,
        )

    async def download_asset(self, asset_id, destination):
        value = 0.25 if asset_id == "0" else -0.25
        sf.write(destination, np.ones(2400) * value, 24000)
        return destination

    async def upload_asset(self, path):
        self.calls.append(("upload", str(path)))
        return Obj(id="uploaded")

    async def register_voice(self, **kwargs):
        self.calls.append(("register", kwargs))
        return Obj(id="registered")

    async def cancel_job(self, job_id):
        self.calls.append(("cancel", job_id))


def make_engine(tmp_path, **options):
    from tts_llm_demo.tts import TTS

    store = Store(tmp_path)
    config = Config(llm=LLMConfig(base_url="http://llm/v1", model="qwen"), storage_dir=tmp_path)
    fake = FakeTTS(**options)
    return TTS(config, store, client=fake), store, fake


def speech():
    return Plan.model_validate(
        {
            "kind": "speech",
            "title": "Negotiation",
            "pause_ms": 100,
            "speakers": [
                {"name": "buyer", "model": "talk", "voice_id": "a"},
                {"name": "seller", "model": "talk", "voice_id": "b"},
            ],
            "turns": [
                {"speaker": "buyer", "text": "Would you take less?"},
                {"speaker": "seller", "text": "Let us negotiate."},
            ],
        }
    )


def design():
    return Plan(
        kind="voice_design",
        title="Older woman",
        model="design",
        description="An older woman with a warm, measured voice.",
        preview_text="Good morning, it is lovely to meet you.",
    )


async def test_a_conversation_uses_stable_voices_and_assembles(tmp_path):
    engine, store, fake = make_engine(tmp_path)
    chat = store.new_chat()["id"]
    result = await engine.execute(speech(), chat, lambda **x: None, asyncio.Event())
    data, rate = sf.read(store.audio_path(result["id"]))
    assert rate == 24000 and len(data) == 7200
    assert data[:2400].mean() > 0 and data[-2400:].mean() < 0
    assert [c[1]["voice"]["id"] for c in fake.calls if c[0] == "speech"] == ["a", "b"]
    assert len(store.list("asset")) == 3


async def test_b_design_without_reuse(tmp_path):
    engine, store, fake = make_engine(tmp_path, cloning=False)
    result = await engine.execute(
        design(), store.new_chat()["id"], lambda **x: None, asyncio.Event()
    )
    assert result["kind"] == "voice_preview"
    assert result["reusable_models"] == []
    with pytest.raises(ValueError, match="cloning"):
        await engine.save_voice(result["id"], "Monica", "clone")
    assert not store.list("voice")


async def test_c_save_reference_and_restore_before_speech(tmp_path):
    engine, store, fake = make_engine(tmp_path)
    chat = store.new_chat()["id"]
    asset = await engine.execute(design(), chat, lambda **x: None, asyncio.Event())
    voice = await engine.save_voice(asset["id"], "Monica", "clone")
    assert voice["name"] == "Monica" and voice["transcript"] == design().preview_text
    plan = Plan(
        kind="speech",
        title="Monica speaks",
        speakers=[{"name": "Monica", "model": "clone", "saved_voice_id": voice["id"]}],
        turns=[{"speaker": "Monica", "text": "Welcome back!"}],
    )
    await engine.execute(plan, chat, lambda **x: None, asyncio.Event())
    assert len([c for c in fake.calls if c[0] == "upload"]) == 2
    assert [c for c in fake.calls if c[0] == "speech"][-1][1]["voice"] == {"id": "registered"}


async def test_d_unsupported_design_fails_before_submission(tmp_path):
    engine, store, fake = make_engine(tmp_path, design=False)
    with pytest.raises(ValueError, match="voice_design"):
        await engine.execute(design(), store.new_chat()["id"], lambda **x: None, asyncio.Event())
    assert not fake.calls and not store.list("asset")


async def test_invalid_later_turn_rejected_before_any_generation(tmp_path):
    engine, store, fake = make_engine(tmp_path)
    plan = speech()
    plan.turns[-1].instructions = "Angrily"
    with pytest.raises(ValueError, match="instructions"):
        await engine.execute(plan, store.new_chat()["id"], lambda **x: None, asyncio.Event())
    assert not [c for c in fake.calls if c[0] == "speech"]


@pytest.mark.parametrize("cleanup", [False, True])
async def test_generation_groups_assets_and_optionally_removes_clips(tmp_path, cleanup):
    engine, store, _ = make_engine(tmp_path)
    store.put("settings", {"id": "workspace", "delete_clips": cleanup})
    chat = store.new_chat()["id"]
    result = await engine.execute(speech(), chat, lambda **x: None, asyncio.Event())
    job = store.list("job")[0]
    assert job["status"] == "succeeded"
    assert all(a["job_id"] == job["id"] for a in store.list("asset"))
    assert len(store.list("asset")) == (1 if cleanup else 3)
    assert len(list(store.audio_dir.glob("*.wav"))) == (1 if cleanup else 3)
    assert result["clips_deleted"] == cleanup
    assert len(result["clips"]) == (0 if cleanup else 2)
    assert sf.info(store.audio_path(result["id"])).frames == 7200
    second = await engine.execute(design(), chat, lambda **x: None, asyncio.Event())
    assert second["job_id"] != result["job_id"]


@pytest.mark.parametrize(
    "failure, status", [(ValueError, "failed"), (asyncio.CancelledError, "cancelled")]
)
async def test_cleanup_keeps_completed_clips_on_failure(tmp_path, failure, status):
    engine, store, fake = make_engine(tmp_path)
    store.put("settings", {"id": "workspace", "delete_clips": True})
    original = fake.generate_speech

    async def fail_second(**kwargs):
        if fake.jobs:
            raise failure("generation failed")
        return await original(**kwargs)

    fake.generate_speech = fail_second
    with pytest.raises(failure, match="generation failed"):
        await engine.execute(speech(), store.new_chat()["id"], lambda **x: None, asyncio.Event())
    assert store.list("job")[0]["status"] == status
    assert len(store.list("asset")) == 1
    assert store.audio_path(store.list("asset")[0]["id"]).is_file()
