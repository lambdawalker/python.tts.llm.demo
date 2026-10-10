# Single-user audio agent

Approved scope: a Python browser demo using remote Qwen through vLLM and the shared
TTS client. One persistent workspace, no login, loopback by default; explicit LAN
binding shares everything. The browser never receives upstream credentials.

The agent discovers installed model profiles, capabilities, feature guidance and voices.
It drafts a structured speech/conversation or voice-design plan, offers review, and
executes after the user chooses generation (including declining review). Review edits
replace the pending plan. Unsupported capabilities fail before submission. Conversations
use one consistent voice per speaker, sequential turn generation, ordered PCM assembly
and configurable pauses. Native multi-speaker is not in the current shared wire schema;
the demo implements the portable per-turn path without inventing extensions.

Chat, Assets and Voices tabs persist in SQLite plus local audio files. Voice design
previews are always downloadable; reusable voice saving requires a compatible cloning
profile and explicit user naming/consent. Store reference audio and transcript, not just
an expiring upstream ID. Re-upload/register saved references for a fresh TTS session.
Playback is an explicit browser action, with a Play fallback for autoplay restrictions.

Implementation: FastAPI, plain HTML/CSS/JS, httpx for vLLM chat completions, the shared
AsyncTTSClient, soundfile/numpy for bounded PCM WAV concatenation. Python >=3.11, uv.
YAML config holds URLs/model name/storage/limits; environment variables hold credentials.
One active agent run across the single-user workspace. Persist progress, run cancellation
and intermediate assets. Restart marks unfinished runs interrupted, never silently reruns.
Use per-submission idempotency keys; never blindly retry ambiguous LLM/TTS writes.
Anonymous TTS credentials remain server-side; refresh only before new work on expiry,
never silently retry an in-flight job under a new owner. Local assets remain accessible.

Tool loop is bounded, validates all JSON with strict Pydantic models, and never executes
shell commands, generated Python, arbitrary URLs or model-supplied paths. Validate plans
against runtime discovery and server preflight. TTS progress is real stage/turn progress.
Generation, save and playback are separate actions; declining review authorizes only the
pending generation. The application offers deterministic UI controls as well as chat.

Validation covers A-D with deterministic remote fixtures, malformed/unsupported tools,
review gates, reference restoration, cross-tab concurrency, persistence, cancellation,
audio order/format, browser UI, packaging and CI. Live vLLM/GPU quality is a separate
operator smoke test because no user's LAN endpoint is available in this environment.
