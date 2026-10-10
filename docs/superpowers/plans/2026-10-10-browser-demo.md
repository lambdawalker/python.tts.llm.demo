# Browser audio agent implementation plan

**Goal:** Implement the approved A-D workflows as a runnable single-user browser demo.
**Architecture:** Explicit vLLM tool loop; deterministic TTS executor and local workspace.
**Tech Stack:** Python 3.11+, FastAPI, httpx, Pydantic, shared TTS client, SQLite, soundfile.
**Spec:** ../specs/2026-10-10-browser-demo.md

## Global constraints
- One workspace; loopback default; no cloud calls or GPU dependencies in this app.
- Only discovered capabilities; user confirmation for generation/save/playback.
- YAML endpoints, environment credentials, durable local assets, no blind write retries.

## Review focus
- Expired credentials must not hide local assets or retry in-flight generation.
- Invalid tool calls and truncated arguments must not produce side effects.
- Repeated clicks/tabs must not create duplicate generation runs.
- Cancellation and restart must retain completed clips without claiming success.
- Voice identity and clip ordering must remain stable across a conversation.

## Tasks
- [ ] Write failing workspace/config/audio tests; implement config.py, store.py,
  schemas.py and audio.py. Validate YAML, durable data and ordered bounded assembly.
- [ ] Write failing A-D executor tests; implement tts.py using AsyncTTSClient.
  Discover profiles, validate plans, generate turns/design, store assets, restore voices.
- [ ] Write failing agent tests; implement llm.py and agent.py with validated tools,
  review gates, explicit save/play actions and bounded histories/turns.
- [ ] Write failing HTTP tests; implement app.py and cli.py with background tasks,
  single-workspace run locking, cancellation, safe asset delivery and origin checks.
- [ ] Build static/index.html, app.js and style.css. Verify chat/review/assets/voices
  through browser automation with deterministic transport fixtures.
- [ ] Add README, config example, agent docs and CI. Run pytest, Ruff, build and
  distribution checks. Review changes, publish PR and inspect hosted CI.

Execution: implement inline using the user's instruction to proceed; no additional
approval gate for the already approved scope.
