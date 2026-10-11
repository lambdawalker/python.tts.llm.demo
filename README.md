# TTS LLM Demo

A single-user browser audio workspace. A remote Qwen model served by **vLLM** plans
speech and voice-design tasks, and the application executes them through
[python.tts.api.client](https://github.com/lambdawalker/python.tts.api.client).
The LLM and TTS model run on your servers; this app does not load model weights.

## Start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/lambdawalker/python.tts.llm.demo.git
cd python.tts.llm.demo
cp config.example.yaml config.yaml
# Edit llm.base_url, llm.model and tts.base_url in config.yaml.
uv sync --frozen --no-dev
uv run --frozen --no-dev tts-llm-demo --config config.yaml
```

On Windows PowerShell, use `Copy-Item config.example.yaml config.yaml`.
Open **http://127.0.0.1:7860**. No login is required. One shared workspace persists
across browser refreshes and application restarts. One task runs at a time.

### LM Studio

The same OpenAI-compatible transport can connect to LM Studio. Set `llm.base_url`
to `http://<windows-lan-ip>:1234/v1` and `llm.model` to the exact model ID returned
by that server's `/v1/models`. Enable access from the network when the demo runs
on another machine. The loaded model and chat template must support tool calling.

The demo sends one leading system message containing instructions and runtime
context, accommodating templates that reject additional system messages. If a
request fails, check the LM Studio console for the underlying template/model error.
This compatibility fix is covered by a simulated template-rejection regression test;
a live Windows LM Studio run still needs verification.

### Remote vLLM

Set `llm.base_url` to the vLLM OpenAI-compatible URL **including `/v1`**, for example
`http://192.168.1.50:8001/v1`. Set `llm.model` to the exact model ID returned by:

```bash
curl http://192.168.1.50:8001/v1/models
```

The sample uses `Qwen/Qwen3.8-27B`; change it if your server uses a served-model alias.
vLLM must support automatic tool calling and a tool parser/chat template appropriate
to your exact model and vLLM version. Configure `--enable-auto-tool-choice` and the
appropriate `--tool-call-parser`; a model-specific reasoning parser should separate
reasoning from final content. Do not copy a parser flag for another Qwen generation.
See the [official vLLM tool-calling guide](https://docs.vllm.ai/en/stable/features/tool_calling/).
This app requests `tool_choice: auto`, validates tool arguments locally, and does not
execute model-generated shell commands or Python. Plain text containing pretend tool
calls is not executed; configure the server to return structured `tool_calls`.

For a protected endpoint, set `llm.api_key_env: VLLM_API_KEY` and provide that environment
variable to the Python process. Keys never go in the browser or model context.

### Remote TTS

Run a compatible shared server, for example the
[DGX Spark Qwen integration](https://github.com/lambdawalker/dgxspark.qwen3TTS):

```bash
unset TTS_API_TOKENS
uv run --frozen --extra api qwen-tts-api --anonymous-sessions
```

Set `tts.base_url` to its root URL, e.g. `http://192.168.1.51:8000` (no `/v1`).
The demo uses an anonymous session and keeps its credential server-side. For static
bearer authentication, set `tts.anonymous_session: false`, `tts.api_key_env: TTS_API_KEY`
and export that variable. For a shared `--no-auth` server, set anonymous_session false
and omit api_key_env. Endpoint URLs are contacted by Python, not by your browser.

The runtime client and development-only server are pinned to immutable session-support
commits in `pyproject.toml` and `uv.lock`. Use `uv` from this checkout so Git source
mappings are honored. A plain `pip install .` does not apply those mappings.

## Try the four workflows

| Ask | What happens |
| --- | --- |
| “Generate a car-sale negotiation with two speakers.” | The agent drafts speaker assignments and ordered turns, offers review, then generates separate clips and joins them with pauses. |
| “Design the voice of an older woman.” — design-only service | Produces a downloadable voice preview. No reusable-voice action is offered when no compatible cloning/TTS profile exists. |
| Same request — design plus cloning profiles | After generation, ask “Save it as Monica,” or use **Save as a reusable voice** in Assets. Later ask “Use Monica to say…” |
| Same request — no design capability | Explains that voice design is unavailable; it can offer discovered preset voices instead. |

**Chat** contains messages, a collapsible editable script/description, Generate now,
Save edits, Discard, progress and Cancel. Say “no, generate it” to decline review.
Generation cannot run in the same agent turn that first proposes a plan. You can also
use the explicit controls without relying on the LLM to interpret your reply.

**Assets** contains full pieces, voice previews and individual turns. Play starts audio
in your browser, even when Python runs elsewhere. If the browser blocks a chat-requested
playback, click Play in the audio player. Audio is never played on the server machine.

**Voices** keeps named references with audio, description, transcript and compatible
model ID. A voice-design profile and its reusable synthesis profile may be different
models. For Qwen, this is typically VoiceDesign followed by Base cloning. The app saves
the reference, not trained model weights; acoustic similarity depends on the TTS model.

## Persistence and recovery

- Storage is under `storage_dir`, resolved relative to your YAML. It contains SQLite
  and WAV files. Keep this directory private; it includes the TTS session credential.
  Back it up as a unit while the app is stopped. No automatic local retention/deletion.
- TTS session expiration and remote asset retention do not delete locally downloaded
  audio. Saved voices are uploaded and registered again before reuse.
- Expired sessions are replaced **before new work**. In-flight failures are not retried
  under a new identity. For a revoked token, click **Reset TTS session**, then explicitly
  retry your task. Reset keeps local assets/voices and forgets only the saved credential;
  it does not delete resources or revoke the old remote session.
- A failed or interrupted run retains completed clips and the last plan. **Revisit last
  plan** restores it for editing and an explicit new generation. This can generate new
  clips; it does not automatically resume or deduplicate an earlier partially completed run.
- Cancellation requests cancellation of a known active remote job. A request accepted
  just before a connection interruption may still finish. Completed local clips remain.
- Restart marks active runs interrupted; it never silently replays submissions.

## Configuration and limits

See [config.example.yaml](config.example.yaml). Runtime defaults: 40 turns, 30 minutes
maximum assembled audio, 8 LLM tool rounds per user message, 15-minute TTS job timeout.
Model-specific input limits, languages, voice requirements and delivery controls are
validated before generation. All speech turns are preflighted before the first speech job.

The current shared API has one voice per speech request; the demo uses the portable
per-turn composition path. It does not claim native multi-speaker synthesis, overlapping
speech, music mixing, resampling, microphone input, or incremental audio streaming.
Speaker profiles must share an output sample rate and channel count for assembly.
Progress reports job stages and completed turns, not an invented GPU percentage.
LLM responses are displayed when each model turn completes; tool/job progress updates
while work runs. Recent chat history is bounded to 30 messages; all messages remain stored.

The server defaults to `127.0.0.1`. For LAN use set `host: 0.0.0.0` and add the address
or hostname you browse to in `allowed_hosts`. **Every visitor gets the same workspace.**
This is not a multi-user/authenticated deployment. Use one process/worker per storage
folder; a process lock prevents opening the same workspace twice.

## Development

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv build
uv run twine check dist/*
```

Tests exercise A–D, review gates, malformed/truncated LLM output, ordered audio, cancellation,
restart, session reset and the actual shared client/server wire contract with fake acoustic
inference. Browser CI runs a deterministic fixture with silence/tones, not your LAN models.

Optional browser smoke (Node is only a development dependency):

```bash
npm install --no-save --package-lock=false playwright@1.62.1
npx playwright install chromium
# Start with a fresh DEMO_TEST_DIR for each run:
DEMO_TEST_DIR=/tmp/tts-demo-smoke uv run python tests/browser_server.py
# In another terminal:
node tests/browser_smoke.cjs
```

See [architecture](docs/architecture.md), [hardware smoke test](docs/smoke-test.md),
and the [central TTS design](https://github.com/lambdawalker/design.ai/tree/main/tts).
No live vLLM, DGX Spark GPU, or acoustic quality claim is implied by fixture tests.

### Managing conversations and generated audio

Use **Delete conversation** in Chat to remove its history and drafts. A confirmation
explains that generated audio and saved voices remain available. Deletion is disabled
while a task is running; cancel or wait first.

Assets are grouped by generation job, with the finished audio first and individual
clips in an expandable section. Each generation attempt has its own group, including
partial results from failed or cancelled jobs. A demo generation job may involve
several remote TTS jobs. Older combined assets are grouped with their recorded clips;
older orphan clips remain under “Earlier ungrouped clips”.

In Assets, enable **Delete individual clips after assembling audio** to remove local
intermediate WAV files and their asset entries after successful assembly. The setting
is off by default, persists across restarts, and applies only to future jobs. Final
audio, voice previews and saved voices remain. Failed/cancelled jobs retain completed
clips. If a file cannot be deleted, it stays listed for recovery. Remote TTS storage
is unaffected.

After generation, the Chat card shows an audio player next to **Revisit last plan**.
Use its play/pause, seek and volume controls to listen without switching tabs.
Playback starts only when requested, and regular workspace polling preserves position.

Each asset has a **Delete** button and a selection checkbox. Select several assets
(or **Select all deletable assets**, including collapsed clips), then choose
**Delete selected** and confirm. Only selected local audio files are removed; deleting
a combined recording does not delete its clips, or vice versa. Saved-voice reference
audio is protected. Deletion is blocked while a task runs. If Windows or another
process locks a file, it stays listed and the UI reports the failed deletion so you
can close its player and retry. Successful deletions update clip lists and playback
references. Remote TTS storage is unaffected.
