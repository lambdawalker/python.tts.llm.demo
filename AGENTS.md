# TTS demo contributor guidance

- Product: single-user browser audio agent; remote vLLM + shared TTS SDK.
- Contract authority: https://github.com/lambdawalker/design.ai/tree/main/tts.
- TTS transport: https://github.com/lambdawalker/python.tts.api.client.
- Model adapter: https://github.com/lambdawalker/dgxspark.qwen3TTS.
- Read docs/architecture.md and config.example.yaml before changing workflows.
- Keep review, generation, voice-save and playback decisions separate.
- Do not invent capabilities, IDs, native multi-speaker syntax or progress percentages.
- Keep runtime secrets out of LLM context, browser state and logs.
- Persist reference audio for saved voices; remote voice IDs are session-scoped.
- Never execute generated code/commands or follow model-supplied URLs/paths.
- Preserve last drafts and completed clips on failure. Do not automatically replay writes.
- Run uv run pytest, Ruff and build checks. Browser fixtures are not GPU validation.
- Use uv sources/lock for immutable shared-package dependencies; no model weights here.
