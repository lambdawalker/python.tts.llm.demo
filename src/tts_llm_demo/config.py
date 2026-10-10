"""Strict configuration; credentials are resolved only on the server."""

from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Endpoint(Strict):
    base_url: str
    api_key_env: str | None = None
    timeout_seconds: float = Field(default=180, gt=0, le=3600)

    @field_validator("base_url")
    @classmethod
    def url(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use an HTTP(S) base URL without credentials, query or fragment")
        return value.rstrip("/")


class LLMConfig(Endpoint):
    model: str = Field(min_length=1)
    max_tokens: int = Field(default=4096, ge=256, le=32768)
    temperature: float = Field(default=0.4, ge=0, le=2)
    max_tool_rounds: int = Field(default=8, ge=1, le=20)


class TTSConfig(Endpoint):
    base_url: str = "http://127.0.0.1:8000"
    anonymous_session: bool = True
    job_timeout_seconds: float = Field(default=900, gt=0, le=7200)
    poll_seconds: float = Field(default=1, ge=0.05, le=30)


class Config(Strict):
    llm: LLMConfig
    tts: TTSConfig = Field(default_factory=TTSConfig)
    host: str = "127.0.0.1"
    allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1", "localhost", "::1"])
    port: int = Field(default=7860, ge=1, le=65535)
    storage_dir: Path = Path(".demo-data")
    max_turns: int = Field(default=40, ge=1, le=100)
    max_audio_seconds: int = Field(default=1800, ge=1, le=7200)


def load_config(path: Path) -> Config:
    config = Config.model_validate(yaml.safe_load(path.read_text()) or {})
    config.storage_dir = (path.resolve().parent / config.storage_dir).resolve()
    return config
