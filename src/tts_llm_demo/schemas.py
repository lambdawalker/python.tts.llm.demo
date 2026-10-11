"""Validated plans and tool arguments shared with the LLM and HTTP API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Speaker(Input):
    name: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1)
    voice_id: str | None = None
    saved_voice_id: str | None = None

    @model_validator(mode="after")
    def exclusive(self):
        if self.voice_id and self.saved_voice_id:
            raise ValueError("Choose a preset/registered voice OR a saved voice")
        return self


class Turn(Input):
    speaker: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=10000)
    instructions: str | None = Field(default=None, max_length=2000)


class Plan(Input):
    kind: Literal["speech", "voice_design"]
    title: str = Field(min_length=1, max_length=120)
    speakers: list[Speaker] = Field(default_factory=list, max_length=12)
    turns: list[Turn] = Field(default_factory=list, max_length=100)
    language: str | None = None
    pause_ms: int = Field(default=250, ge=0, le=5000)
    model: str | None = None
    description: str | None = Field(default=None, max_length=10000)
    preview_text: str | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def consistent(self):
        if self.kind == "speech":
            names = [s.name for s in self.speakers]
            if not self.turns or len(set(names)) != len(names):
                raise ValueError("Speech needs turns and unique speaker names")
            if any(t.speaker not in names for t in self.turns):
                raise ValueError("Every turn needs a declared speaker")
        elif not self.model or not self.description or not self.preview_text:
            raise ValueError("Voice design needs model, description and preview_text")
        return self


class Empty(Input):
    pass


class AssetInput(Input):
    asset_id: str


class SaveVoice(AssetInput):
    name: str = Field(min_length=1, max_length=80)
    model: str


class Message(Input):
    text: str = Field(min_length=1, max_length=12000)


class Action(Input):
    action: Literal["generate", "discard", "restore", "save", "play"]
    asset_id: str | None = None
    name: str | None = None
    model: str | None = None


class Settings(Input):
    delete_clips: bool = False


class DeleteAssets(Input):
    asset_ids: list[str] = Field(min_length=1, max_length=1000)


class DeleteChats(Input):
    chat_ids: list[str] = Field(min_length=1, max_length=1000)
