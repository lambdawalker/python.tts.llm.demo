"""Minimal OpenAI-compatible chat transport, with no write retries."""

import os
import re

import httpx


def final_text(text):
    if not isinstance(text, str):
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


class LLM:
    def __init__(self, config, transport=None):
        self.config = config
        key = os.environ.get(config.api_key_env) if config.api_key_env else None
        if config.api_key_env and not key:
            raise ValueError(f"Set environment variable {config.api_key_env}")
        self.http = httpx.AsyncClient(
            base_url=config.base_url + "/",
            timeout=config.timeout_seconds,
            trust_env=False,
            headers={"Authorization": f"Bearer {key}"} if key else {},
            transport=transport,
        )

    async def complete(self, messages, tools):
        response = await self.http.post(
            "chat/completions",
            json={
                "model": self.config.model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
                "stream": False,
            },
        )
        if not response.is_success:
            raise ValueError(
                f"LLM server returned HTTP {response.status_code}. Check the configured model, "
                "API key and tool-calling/chat-template settings; "
                "see the LLM server logs for details."
            )
        try:
            choice = response.json()["choices"][0]
            message = choice["message"]
            if choice.get("finish_reason") == "length":
                raise ValueError(
                    "LLM server output was truncated; increase max_tokens or shorten the task"
                )
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError("Invalid assistant response from LLM server")
            # Do not persist/send private reasoning fields as ordinary user-visible text.
            result = {"role": "assistant", "content": final_text(message.get("content")) or None}
            if message.get("tool_calls"):
                if not isinstance(message["tool_calls"], list) or len(message["tool_calls"]) > 12:
                    raise ValueError("Invalid or excessive tool calls from LLM server")
                result["tool_calls"] = message["tool_calls"]
            return result
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("LLM server returned an invalid chat completion") from exc

    async def close(self):
        await self.http.aclose()
