"""Deterministic UI smoke fixture. Never used by the production launcher."""

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
app = create_app(config, tts_client=FakeTTS(), llm=ScriptedLLM(replies))
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=7861)
