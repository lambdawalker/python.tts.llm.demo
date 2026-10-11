"""Deterministic UI smoke fixture. Never used by the production launcher."""

import asyncio
import os
from pathlib import Path

import uvicorn
from test_agent import ScriptedLLM, call
from test_tts import FakeTTS, design, speech

from tts_llm_demo.app import create_app
from tts_llm_demo.config import Config, LLMConfig

config = Config(
    llm=LLMConfig(base_url="http://unused/v1", model="fixture"),
    storage_dir=Path(os.environ.get("DEMO_TEST_DIR", "/tmp/tts-demo-browser")),
)
replies = [
    call("propose_plan", speech().model_dump()),
    {
        "role": "assistant",
        "content": "I drafted a buyer–seller negotiation. Would you like "
        "to review the script first, or generate now?",
    },
    call("propose_plan", design().model_dump()),
    {
        "role": "assistant",
        "content": "The voice description is ready. Review it or generate "
        "a preview. You can then save it as Monica.",
    },
]
replies.extend(
    [
        call("propose_plan", speech().model_dump()),
        {"role": "assistant", "content": "Ready for another generation."},
    ]
)
replies.extend(
    [
        call("propose_plan", speech().model_dump()),
        {"role": "assistant", "content": "Review this cancellable generation."},
    ]
)


class BrowserTTS(FakeTTS):
    async def generate_speech(self, **kwargs):
        # Keep real run lifecycle visible long enough to exercise progress and Cancel.
        await asyncio.sleep(1)
        return await super().generate_speech(**kwargs)


app = create_app(config, tts_client=BrowserTTS(), llm=ScriptedLLM(replies))
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=7861)
