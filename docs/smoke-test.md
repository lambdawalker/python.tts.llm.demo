# Live deployment acceptance

1. Run vLLM with the exact Qwen checkpoint, automatic tool calling and a compatible parser.
   Confirm `/v1/models` returns the configured model. Run TTS with anonymous sessions.
2. Set YAML endpoints and launch the demo. Ask “What audio operations are available?”
   Confirm the answer matches installed profiles rather than upstream model marketing.
3. Ask for a two-speaker car sale negotiation. Review/edit the script, generate, inspect
   completed-turn progress and listen to the combined WAV. Verify distinct stable voices,
   correct line order, pauses and complete words. Fixture audio cannot validate these.
4. Ask for an older woman's voice. Decline description review. Play the resulting preview.
   If a compatible cloning profile is installed, save it as Monica and generate new text
   with Monica. Listen for speaker similarity and correct pronunciation.
5. Reset TTS session, then generate with Monica again. Confirm stored assets remain playable.
6. Repeat with only a design profile installed: the preview should remain downloadable
   without a reusable-voice option. Repeat with no design profile: the agent should explain
   the limitation without submitting a design job.
7. Cancel a multi-turn task, then inspect completed clips. Restart mid-run and confirm it
   becomes interrupted without automatic regeneration. Revisit the last plan explicitly.

Model/parser behavior is version-dependent. If the model writes tool calls as prose, or
vLLM rejects `tool_choice: auto`, correct the serving configuration. The app does not parse
arbitrary generated code as executable actions. Large models sharing one GPU with TTS
may need separate devices or memory budgets; this demo does not manage model servers.
