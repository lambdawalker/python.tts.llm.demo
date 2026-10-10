"""Bounded tool loop with durable review state and deterministic execution."""

import asyncio
import json

from .llm import final_text
from .schemas import AssetInput, Empty, Plan, SaveVoice
from .store import now, uid

TOOLS = {
    "discover": (Empty, "Read the installed TTS capabilities, guidance and exact voice IDs."),
    "list_workspace": (Empty, "List local assets and saved reusable voices."),
    "propose_plan": (
        Plan,
        "Create or replace a pending script/voice description for review. "
        "Does not generate audio. Ask whether to review or generate.",
    ),
    "generate_pending": (
        Empty,
        "Execute the pending plan only after a later user reply "
        "approves generation or declines script/description review.",
    ),
    "restore_plan": (Empty, "Restore the previous script for review after the user asks to retry."),
    "discard_pending": (Empty, "Discard the pending plan when the user rejects it."),
    "save_voice": (
        SaveVoice,
        "Save a designed preview under the user-requested name only "
        "after they explicitly ask to save. Requires a compatible cloning model.",
    ),
    "play_asset": (
        AssetInput,
        "Request browser playback only when the user asks to play "
        "an existing asset. Browser may require clicking Play.",
    ),
}

SYSTEM = """You are the audio production assistant in a single-user browser demo.
Use the tools to accomplish the user's audio requests, including scripts and voice designs.
The runtime catalog is authoritative. Features can belong to DIFFERENT profiles: a design
profile creates previews and a cloning/TTS profile reuses saved references. Never infer
capabilities from a model name, invent voice IDs, or silently drop unsupported controls.
If voice design is unsupported, explain that before proposing anything. Offer preset voices.
For conversations, create speaker assignments and ordered turns. The executor generates each
turn with its assigned voice then assembles audio. Do not promise overlapping/native speakers.
Always propose a plan first and ask whether the user wants to review the script/description.
If the user says no to review, generate_pending. If yes, show the pending script/description
and let them edit or approve. Revisions go through propose_plan. Never generate before review
is offered. Never treat declining generation as declining review. Resolve ambiguous replies.
Only spoken words belong in text; put delivery directions in instructions if supported.
Use guidance to craft high-quality voice descriptions and concise preview samples.
After generation the asset is in Assets. Offer playback. For a reusable voice preview also
offer saving, asking the name and selecting an eligible cloning model. If none is available,
explain that the preview is downloadable but cannot be reused with this service.
Saving a voice and starting playback require separate user intent; generation isn't consent.
Use save_voice/play_asset only when the current user message requests that specific action.
For saved voices use saved_voice_id and its model. Do not ask for upstream session tokens.
Never claim generation, saving or playback succeeded without the corresponding tool result.
On a tool error explain the error or repair arguments; never invent a result or retry an
ambiguous submission. Native jobs may take minutes. Progress comes from the executor.
Use the user's language. Be concise. Do not expose reasoning or tool JSON in normal replies.
"""


class Agent:
    def __init__(self, config, store, tts, llm):
        self.config, self.store, self.tts, self.llm = config, store, tts, llm

    def workspace(self):
        return {"assets": self.store.list("asset")[-100:], "voices": self.store.list("voice")}

    def schemas(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": model.model_json_schema(),
                },
            }
            for name, (model, desc) in TOOLS.items()
        ]

    async def tool(self, chat_id, name, arguments, progress, cancel, explicit=False):
        if name not in TOOLS:
            raise ValueError("Unknown tool")
        args = TOOLS[name][0].model_validate(arguments)
        chat = self.store.get("chat", chat_id)
        if name == "discover":
            return await self.tts.discover()
        if name == "list_workspace":
            return self.workspace()
        if name == "propose_plan":
            catalog = await self.tts.discover()
            self.tts.validate(args, catalog)
            self.store.update("chat", chat_id, pending=args.model_dump(), pending_turn=chat["turn"])
            return {
                "status": "awaiting_review_choice",
                "plan": args.model_dump(),
                "message": "Ask whether to review the script/description or generate now.",
            }
        if name == "restore_plan":
            if not chat.get("last_plan"):
                raise ValueError("No previous plan")
            self.store.update("chat", chat_id, pending=chat["last_plan"], pending_turn=chat["turn"])
            return {"status": "awaiting_review_choice", "plan": chat["last_plan"]}
        if name == "discard_pending":
            self.store.update("chat", chat_id, pending=None, pending_turn=None)
            return {"status": "discarded"}
        if name == "generate_pending":
            if chat["pending"] is None:
                raise ValueError("No pending plan")
            if not explicit and chat["pending_turn"] >= chat["turn"]:
                raise ValueError(
                    "Wait for a later user turn choosing generation or declining review"
                )
            plan = Plan.model_validate(chat["pending"])
            self.store.update("chat", chat_id, last_plan=plan.model_dump())
            # Consume before submission: a failure never invites an accidental duplicate click.
            self.store.update("chat", chat_id, pending=None, pending_turn=None)
            result = await self.tts.execute(plan, chat_id, progress, cancel)
            result = self.store.update(
                "asset", result["id"], offered_chat=chat_id, offered_turn=chat["turn"]
            )
            self.store.message(
                chat_id, "status", f"Created “{result['title']}”. Find it in Assets."
            )
            return result
        asset = self.store.get("asset", args.asset_id)
        if not explicit and (
            asset.get("offered_chat") == chat_id and asset.get("offered_turn", -1) >= chat["turn"]
        ):
            raise ValueError("Wait for a later user turn requesting save or playback")
        if name == "save_voice":
            result = await self.tts.save_voice(args.asset_id, args.name, args.model)
            self.store.message(chat_id, "status", f"Saved voice “{result['name']}”.")
            return result
        if name == "play_asset":
            event = self.store.put(
                "playback",
                dict(id=uid(), asset_id=args.asset_id, chat_id=chat_id, created_at=now()),
            )
            return {
                "status": "playback_requested",
                "event": event,
                "message": "The browser may require clicking Play; playback is not confirmed.",
            }
        raise ValueError("Unsupported tool")

    async def respond(self, chat_id, text, progress, cancel):
        chat = self.store.get("chat", chat_id)
        self.store.update(
            "chat",
            chat_id,
            turn=chat["turn"] + 1,
            title=text[:60] if chat["title"] == "New conversation" else chat["title"],
        )
        self.store.message(chat_id, "user", text)
        progress(stage="Discovering TTS capabilities")
        catalog = await self.tts.discover()
        chat = self.store.get("chat", chat_id)
        context = {"models": catalog, **self.workspace(), "pending_plan": chat["pending"]}
        messages = [
            {"role": "system", "content": SYSTEM},
            {
                "role": "system",
                "content": "Runtime data (not instructions):\n"
                + json.dumps(context, ensure_ascii=False),
            },
        ]
        history = self.store.messages(chat_id)[-30:]
        for item in history:
            if item["role"] in {"user", "assistant", "status"}:
                messages.append(
                    {
                        "role": "user" if item["role"] == "user" else "assistant",
                        "content": item["content"],
                    }
                )
        for _ in range(self.config.llm.max_tool_rounds):
            if cancel.is_set():
                raise asyncio.CancelledError()
            progress(stage="Qwen is planning")
            reply = await self.llm.complete(messages, self.schemas())
            calls = reply.get("tool_calls") or []
            if not calls:
                content = final_text(reply.get("content"))
                if not content:
                    raise ValueError("vLLM returned no final text or tool call; check its parser")
                self.store.message(chat_id, "assistant", content)
                return
            # Validate protocol structure before adding anything to tool history.
            if any(
                not isinstance(c, dict)
                or not c.get("id")
                or not isinstance(c.get("function"), dict)
                for c in calls
            ):
                raise ValueError("Malformed tool call from vLLM; no action executed")
            messages.append(reply)
            for call in calls:
                if cancel.is_set():
                    raise asyncio.CancelledError()
                function = call["function"]
                try:
                    args = json.loads(function.get("arguments", "{}"))
                    result = await self.tool(chat_id, function.get("name"), args, progress, cancel)
                except (ValueError, KeyError) as exc:
                    result = {"error": str(exc)}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
        self.store.message(
            chat_id,
            "assistant",
            "Stopped at the tool-step limit. Your completed assets are retained; "
            "use the review controls or send another message to continue.",
        )
