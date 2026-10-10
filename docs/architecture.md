# Architecture

The browser talks only to this app. FastAPI serves packaged HTML/CSS/JS and workspace
routes. The backend connects independently to remote vLLM and the shared TTS service.
A global run task enforces single-user, single-active-task semantics across tabs.
The UI polls persisted state once per second; navigation/reload does not cancel jobs.

- `config.py`: strict YAML schema and URLs, environment-variable names for credentials.
- `schemas.py`: strict plans, speaker/turn definitions and UI action bodies.
- `store.py`: SQLite records and application-generated local asset paths.
- `llm.py`: vLLM chat completion transport; no retries; excludes reasoning fields.
- `agent.py`: bounded tools, prompt, contextual discovery, pending plan and consent turns.
- `tts.py`: shared SDK, profile validation, preflight, jobs, capture, saved voice restoration.
- `audio.py`: blockwise PCM WAV assembly with pauses, format and duration checks.
- `app.py`: run lifecycle, HTTP boundary, browser assets and audio range/file serving.

The agent sees installed profiles, capability documents, native feature guidance, exact
voice IDs, saved voices, recent assets and the pending plan. It can propose a structured
plan, generate the pending plan, discard/restore it, save a named voice, request playback,
or refresh discovery/workspace state. Unknown tools and malformed arguments have no effect.
API errors terminate the run; a generated explanation never substitutes for a successful tool.

Review gates are application state, not only a prompt. A plan records its proposal turn;
generation needs a later user turn or a direct Generate click. A newly generated result
cannot be saved/played by the agent in that same turn. The model interprets natural-language
intent on subsequent turns; UI buttons provide deterministic explicit actions.

A pending plan is consumed before execution to prevent duplicate submissions. A separate
last-plan copy remains durable for explicit recovery. No automatic retry on timeout or
connection failure. Idempotency keys are per submitted request, not a guarantee of resuming
an interrupted whole conversation. Remote generation is serial to suit a shared GPU.

Voice design and voice cloning are separate capabilities. The demo considers a ready/loadable
cloning+TTS profile with a sufficient reference-duration limit a candidate; actual upload and
registration validate compatibility. Persist the downloaded reference and transcript, then
register again before use, so expired sessions do not strand the local voice library.

Trust boundary: one local owner. Allowed Host values, a same-origin mutation header, Origin
validation, restrictive CSP, and text-only DOM rendering protect the local browser surface.
They do not implement accounts or LAN-user isolation. Runtime credentials never enter
`/api/state`, tool context, or browser storage. Restrict access to the data directory.
