"""Capability-aware execution through the shared TTS SDK."""

import asyncio
import os
import time
from datetime import datetime, timezone

import soundfile as sf
from tts_api_client import AsyncTTSClient

from .audio import assemble
from .schemas import Plan
from .store import now, uid


def supported(cap, feature):
    return cap.get("features", {}).get(feature, {}).get("supported") is True


class TTS:
    def __init__(self, config, store, client=None):
        self.config, self.store = config, store
        self.client = client
        self.injected = client is not None
        self.session_expiry = None

    async def connect(self):
        if self.injected:
            return
        settings = self.config.tts
        if self.client and (
            self.session_expiry is None or self.session_expiry > datetime.now(timezone.utc)
        ):
            return
        if self.client:
            await self.client.aclose()
        key = os.environ.get(settings.api_key_env) if settings.api_key_env else None
        if settings.api_key_env and not key:
            raise ValueError(f"Set environment variable {settings.api_key_env}")
        if key and settings.anonymous_session:
            raise ValueError("Choose anonymous sessions or an API key, not both")
        saved = None
        if settings.anonymous_session:
            try:
                candidate = self.store.get("secret", "tts_session")
                if candidate["url"] == settings.base_url and datetime.fromisoformat(
                    candidate["expires_at"]
                ) > datetime.now(timezone.utc):
                    saved = candidate
            except KeyError:
                pass
        self.client = AsyncTTSClient(
            settings.base_url,
            api_key=key,
            anonymous_session=settings.anonymous_session,
            session_token=saved["access_token"] if saved else None,
            timeout=settings.timeout_seconds,
        )
        if settings.anonymous_session:
            if saved is None:
                try:
                    session = await self.client.create_session()
                except Exception:
                    await self.client.aclose()
                    self.client = None
                    raise
                saved = session.model_dump()
                self.store.put("secret", {**saved, "id": "tts_session", "url": settings.base_url})
            self.session_expiry = datetime.fromisoformat(saved["expires_at"])

    async def reset_session(self):
        if not self.config.tts.anonymous_session:
            raise ValueError("Session reset is only available in anonymous-session mode")
        await self.close()
        if not self.injected:
            self.client = None
        self.session_expiry = None
        self.store.delete("secret", "tts_session")

    async def close(self):
        if self.client and not self.injected:
            await self.client.aclose()

    async def discover(self):
        await self.connect()
        models = {}
        for profile in await self.client.list_models():
            cap = (await self.client.capabilities(profile.id)).model_dump()
            guidance = {}
            for feature in ("tts", "voice_design", "voice_cloning"):
                if supported(cap, feature):
                    guidance[feature] = (
                        await self.client.guidance(feature, profile.id)
                    ).model_dump()
            models[profile.id] = {
                "capabilities": cap,
                "guidance": guidance,
                "voices": [v.model_dump() for v in await self.client.list_voices(profile.id)],
            }
        return models

    @staticmethod
    def require(catalog, model, feature):
        if model not in catalog:
            raise ValueError(f"Unknown model: {model}")
        cap = catalog[model]["capabilities"]
        if cap.get("availability") not in (None, "ready", "loadable"):
            raise ValueError(f"Model {model} is unavailable")
        if not supported(cap, feature):
            raise ValueError(f"Model {model} does not support {feature}")
        if "wav" not in cap.get("output", {}).get("formats", []):
            raise ValueError("This demo requires WAV output")
        return cap

    def validate(self, plan: Plan, catalog):
        if len(plan.turns) > self.config.max_turns:
            raise ValueError("Plan exceeds configured turn limit")
        if plan.kind == "voice_design":
            cap = self.require(catalog, plan.model, "voice_design")
            self.text_limit(plan.description, cap)
            self.text_limit(plan.preview_text, cap)
            self.language(plan.language, cap)
            return
        speakers = {s.name: s for s in plan.speakers}
        rates = None
        for speaker in plan.speakers:
            cap = self.require(catalog, speaker.model, "tts")
            available_rates = set(cap.get("output", {}).get("sample_rates", []))
            rates = available_rates if rates is None else rates & available_rates
            if speaker.saved_voice_id:
                self.require(catalog, speaker.model, "voice_cloning")
                saved = self.store.get("voice", speaker.saved_voice_id)
                if saved["model"] != speaker.model:
                    raise ValueError("Saved voice belongs to a different model profile")
            elif speaker.voice_id:
                if speaker.voice_id not in {v["id"] for v in catalog[speaker.model]["voices"]}:
                    raise ValueError("Select a voice returned by discovery")
            elif cap.get("voice_required"):
                raise ValueError("This model requires an explicit voice")
            self.language(plan.language, cap)
        if not rates:
            raise ValueError("Speaker models need a common sample rate for assembly")
        for turn in plan.turns:
            cap = catalog[speakers[turn.speaker].model]["capabilities"]
            self.text_limit(turn.text, cap)
            if turn.instructions and not cap.get("controls", {}).get("instructions", {}).get(
                "supported"
            ):
                raise ValueError("This speaker model does not support instructions")

    @staticmethod
    def text_limit(text, cap):
        if len(text) > cap.get("limits", {}).get("text_characters", 10000):
            raise ValueError("Text exceeds model's character limit")

    @staticmethod
    def language(language, cap):
        if language is not None and language not in cap.get("languages", []):
            raise ValueError(f"Unsupported language: {language}")

    async def restore(self, asset_id, transcript, model):
        uploaded = await self.client.upload_asset(self.store.audio_path(asset_id))
        voice = await self.client.register_voice(
            model=model, references=[{"asset_id": uploaded.id, "transcript": transcript}]
        )
        return {"id": voice.id}

    async def wait(self, job, progress, cancel):
        deadline = time.monotonic() + self.config.tts.job_timeout_seconds
        try:
            while True:
                if cancel.is_set():
                    raise asyncio.CancelledError()
                status = await self.client.get_job(job.id)
                progress(stage=f"TTS: {status.stage or status.status}", job_id=job.id)
                if status.status == "succeeded":
                    if not status.results:
                        raise ValueError("TTS completed without an audio result")
                    return status.results[0].asset_id
                if status.status in {"failed", "cancelled"}:
                    raise ValueError(
                        f"TTS job {status.status}: "
                        f"{status.error.message if status.error else 'no audio'}"
                    )
                if time.monotonic() >= deadline:
                    raise TimeoutError("TTS job timed out; cancellation requested")
                await asyncio.sleep(self.config.tts.poll_seconds)
        except (asyncio.CancelledError, TimeoutError):
            try:
                await asyncio.wait_for(self.client.cancel_job(job.id), timeout=5)
            except Exception:
                progress(stage="Could not confirm remote cancellation; inspect the TTS server.")
            raise

    async def capture(self, remote_id, chat_id, title, kind, **metadata):
        identifier = uid()
        path = self.store.audio_path(identifier)
        try:
            await self.client.download_asset(remote_id, path)
            info = sf.info(path)
            if info.duration > self.config.max_audio_seconds:
                raise ValueError("Audio exceeds configured duration limit")
            return self.store.put(
                "asset",
                dict(
                    id=identifier,
                    chat_id=chat_id,
                    title=title,
                    kind=kind,
                    created_at=now(),
                    duration=info.duration,
                    sample_rate=info.samplerate,
                    **metadata,
                ),
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    async def execute(self, plan, chat_id, progress, cancel):
        job_id = uid()
        self.store.put(
            "job",
            dict(id=job_id, chat_id=chat_id, title=plan.title, created_at=now(), status="running"),
        )
        delete_clips = self.store.settings()["delete_clips"]
        try:
            result = await self._execute(plan, chat_id, progress, cancel, job_id)
        except asyncio.CancelledError:
            self.store.update("job", job_id, status="cancelled")
            raise
        except Exception:
            self.store.update("job", job_id, status="failed")
            raise
        self.store.update("job", job_id, status="succeeded")
        # Only remove inputs once the combined output is durably saved. A cleanup
        # error must not turn successful generation into a failure or trigger replay.
        if delete_clips and result.get("clips"):
            retained = []
            for clip_id in result["clips"]:
                try:
                    self.store.audio_path(clip_id).unlink(missing_ok=True)
                except OSError:
                    retained.append(clip_id)
                    continue
                self.store.delete("asset", clip_id)
            result = self.store.update(
                "asset", result["id"], clips=retained, clips_deleted=not retained
            )
        return result

    async def _execute(self, plan, chat_id, progress, cancel, job_id):
        catalog = await self.discover()
        self.validate(plan, catalog)
        if cancel.is_set():
            raise asyncio.CancelledError()
        if plan.kind == "voice_design":
            progress(stage="Designing voice", completed=0, total=1)
            job = await self.client.design_voice(
                model=plan.model,
                description=plan.description,
                preview_text=plan.preview_text,
                language=plan.language,
                output={"format": "wav"},
                idempotency_key=uid(),
            )
            remote = await self.wait(job, progress, cancel)
            asset = await self.capture(
                remote,
                chat_id,
                plan.title,
                "voice_preview",
                job_id=job_id,
                transcript=plan.preview_text,
                description=plan.description,
                model=plan.model,
                reusable_models=[],
            )
            compatible = [
                m
                for m, item in catalog.items()
                if supported(item["capabilities"], "voice_cloning")
                and supported(item["capabilities"], "tts")
                and item["capabilities"].get("availability") in (None, "ready", "loadable")
                and asset["duration"]
                <= item["capabilities"].get("references", {}).get("max_duration_seconds", 60)
            ]
            return self.store.update("asset", asset["id"], reusable_models=compatible)
        voices, rates = {}, None
        for speaker in plan.speakers:
            available = set(catalog[speaker.model]["capabilities"]["output"]["sample_rates"])
            rates = available if rates is None else rates & available
            if speaker.saved_voice_id:
                saved = self.store.get("voice", speaker.saved_voice_id)
                voices[speaker.name] = await self.restore(
                    saved["asset_id"], saved["transcript"], speaker.model
                )
            else:
                voices[speaker.name] = {"id": speaker.voice_id} if speaker.voice_id else None
        speakers = {s.name: s for s in plan.speakers}
        requests = []
        for turn in plan.turns:
            request = dict(
                model=speakers[turn.speaker].model,
                text=turn.text,
                voice=voices[turn.speaker],
                language=plan.language,
                instructions=turn.instructions,
                output={"format": "wav", "sample_rate": min(rates)},
            )
            await self.client.validate_speech(**request)
            requests.append(request)
        clips = []
        for index, (turn, request) in enumerate(zip(plan.turns, requests, strict=True)):
            if cancel.is_set():
                raise asyncio.CancelledError()
            progress(stage=f"Generating {turn.speaker}", completed=index, total=len(requests))
            job = await self.client.generate_speech(**request, idempotency_key=uid())
            remote = await self.wait(job, progress, cancel)
            clip = await self.capture(
                remote,
                chat_id,
                f"{plan.title} · {index + 1}: {turn.speaker}",
                "clip",
                job_id=job_id,
                transcript=turn.text,
                speaker=turn.speaker,
                model=request["model"],
            )
            clips.append(clip)
            progress(completed=index + 1)
        if cancel.is_set():
            raise asyncio.CancelledError()
        progress(stage="Assembling audio")
        identifier = uid()
        path = self.store.audio_path(identifier)
        await asyncio.to_thread(
            assemble,
            [self.store.audio_path(c["id"]) for c in clips],
            path,
            plan.pause_ms,
            self.config.max_audio_seconds,
        )
        info = sf.info(path)
        return self.store.put(
            "asset",
            dict(
                id=identifier,
                chat_id=chat_id,
                title=plan.title,
                kind="conversation" if len(clips) > 1 else "speech",
                created_at=now(),
                duration=info.duration,
                sample_rate=info.samplerate,
                job_id=job_id,
                clips_deleted=False,
                clips=[c["id"] for c in clips],
                plan=plan.model_dump(),
            ),
        )

    async def save_voice(self, asset_id, name, model):
        if not name.strip():
            raise ValueError("Provide a voice name")
        if any(v["name"].casefold() == name.strip().casefold() for v in self.store.list("voice")):
            raise ValueError("A voice already has that name; choose another")
        asset = self.store.get("asset", asset_id)
        if asset["kind"] != "voice_preview":
            raise ValueError("Only a designed voice preview can be saved as a voice")
        catalog = await self.discover()
        cap = self.require(catalog, model, "voice_cloning")
        self.require(catalog, model, "tts")
        if asset["duration"] > cap.get("references", {}).get("max_duration_seconds", 60):
            raise ValueError("Preview is too long for this cloning model")
        await self.restore(asset_id, asset["transcript"], model)
        return self.store.put(
            "voice",
            dict(
                id=uid(),
                name=name.strip(),
                asset_id=asset_id,
                transcript=asset["transcript"],
                description=asset["description"],
                model=model,
                created_at=now(),
            ),
        )
