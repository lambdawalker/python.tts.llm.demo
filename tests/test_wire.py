"""Real SDK + ASGI API + worker/storage; only acoustic inference is fake."""

import asyncio

import httpx
import soundfile as sf
from tts_api_client import AsyncTTSClient
from tts_api_server.fake import FakeAdapter
from tts_api_server.http import create_app
from tts_api_server.service import Settings

from tts_llm_demo.config import Config, LLMConfig
from tts_llm_demo.schemas import Plan
from tts_llm_demo.store import Store
from tts_llm_demo.tts import TTS


async def test_real_shared_api_design_save_and_reuse_after_new_session(tmp_path):
    app = create_app(
        [FakeAdapter()], Settings(data_dir=tmp_path / "server", anonymous_sessions=True)
    )
    store = Store(tmp_path / "demo")
    config = Config(
        llm=LLMConfig(base_url="http://unused/v1", model="qwen"), storage_dir=tmp_path / "demo"
    )
    config.tts.poll_seconds = 0.05
    async with app.router.lifespan_context(app):
        async with AsyncTTSClient(
            "http://tts", anonymous_session=True, transport=httpx.ASGITransport(app=app)
        ) as client:
            engine = TTS(config, store, client=client)
            chat = store.new_chat()["id"]
            plan = Plan(
                kind="voice_design",
                title="Monica preview",
                model="fake-v1",
                description="An older woman with a warm delivery.",
                preview_text="Welcome home.",
            )
            asset = await engine.execute(plan, chat, lambda **x: None, asyncio.Event())
            assert asset["reusable_models"] == ["fake-v1"]
            saved = await engine.save_voice(asset["id"], "Monica", "fake-v1")
            await client.revoke_session()
        async with AsyncTTSClient(
            "http://tts", anonymous_session=True, transport=httpx.ASGITransport(app=app)
        ) as new_client:
            engine = TTS(config, store, client=new_client)
            plan = Plan(
                kind="speech",
                title="Monica returns",
                speakers=[{"name": "Monica", "model": "fake-v1", "saved_voice_id": saved["id"]}],
                turns=[{"speaker": "Monica", "text": "Hello again."}],
            )
            result = await engine.execute(plan, chat, lambda **x: None, asyncio.Event())
            assert sf.info(store.audio_path(result["id"])).samplerate == 24000
            assert len(store.list("voice")) == 1
    store.close()
