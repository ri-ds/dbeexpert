"""
llm.py

OpenAI access for the whole backend: one shared async client, a concurrency
gate, and a tolerant JSON helper.

Two details drive the design here:

1. The newer OpenAI reasoning models reject `temperature` and use
   `max_completion_tokens` instead of `max_tokens`, so neither legacy
   parameter is ever sent.
2. JSON mode is used wherever the prompt genuinely always returns an object.
   The original notebook prompts allowed a bare string "NONE" as an answer,
   which JSON mode forbids, so those prompts were reshaped to return an
   explicit boolean field instead. `parse_json` still tolerates fenced blocks
   and a literal NONE so a stray response cannot take a request down.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from openai import AsyncOpenAI

from .settings import settings

log = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None
# An asyncio.Semaphore binds to the running loop, so it is created lazily per
# loop rather than at import time.
_semaphores: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to the .env file at the project root."
            )
        _client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=float(settings.request_timeout_s),
            # The SDK default, which is what the baseline app gets from a bare
            # AsyncOpenAI(). Retrying more often would mask a failure the
            # baseline surfaces as an error.
            max_retries=2,
        )
    return _client


def get_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(settings.max_concurrency)
        _semaphores[loop] = sem
    return sem


async def chat(
    system: str,
    user: str,
    *,
    json_mode: bool = False,
    model: str | None = None,
    max_completion_tokens: int | None = None,
) -> str:
    """
    Single turn completion. Returns the raw assistant text.

    `max_completion_tokens` is left unset for the pipeline calls, which need room
    to finish their JSON, and set only where the output length is known to be
    tiny, such as naming a conversation.
    """
    client = get_client()
    kwargs: dict[str, Any] = {
        "model": model or settings.chat_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if max_completion_tokens is not None:
        kwargs["max_completion_tokens"] = max_completion_tokens

    async with get_semaphore():
        response = await client.chat.completions.create(**kwargs)

    # Log usage for the capped calls, which exist specifically to be cheap. This
    # makes the cost of conversation naming verifiable rather than asserted.
    if max_completion_tokens is not None:
        usage = getattr(response, "usage", None)
        if usage is not None:
            log.info(
                "capped call to %s used %s prompt + %s completion = %s tokens",
                kwargs["model"],
                getattr(usage, "prompt_tokens", "?"),
                getattr(usage, "completion_tokens", "?"),
                getattr(usage, "total_tokens", "?"),
            )

    content = response.choices[0].message.content
    return (content or "").strip()


async def chat_json(system: str, user: str, *, model: str | None = None) -> Any:
    """Completion in JSON mode, parsed. Returns None when nothing usable came back."""
    raw = await chat(system, user, json_mode=True, model=model)
    return parse_json(raw)


async def chat_strict_json(system: str, user: str, *, model: str | None = None) -> Any:
    """
    Completion with NO response_format, parsed strictly.

    This is the shape the baseline app's pipeline calls use: a plain two message
    completion with no JSON mode, whose reply is either valid JSON or the bare
    sentinel string NONE. Both of those behaviours matter.

    JSON mode constrains the model's output space, so the same prompt against
    the same evidence yields different text with it on. And the tolerant
    `parse_json` below rescues malformed replies that the baseline discards,
    which turns a dropped faculty member into a kept one. Neither is wrong, but
    both diverge, so the pipeline uses this instead.
    """
    raw = await chat(system, user, json_mode=False, model=model)
    return parse_strict(raw)


def parse_strict(raw: str) -> Any:
    """
    Exactly the baseline app's parse: `json.loads` inside a bare try/except,
    with the literal string NONE treated as "no result".

    No fence stripping and no brace scavenging, matching llm_utils.py in the
    baseline. A reply this cannot read is one the baseline could not read either,
    and it must be discarded the same way.

    Note what that costs if the chat model is ever changed: gpt-4o wraps its JSON
    in a ```json fence, which this discards, so every judge and extract reply
    gets dropped and answers collapse to "No matching faculty were found for
    that question." Verified against the live API. That is the baseline's
    behaviour too, not a bug introduced here, but it does make the chat model and
    this parser a matched pair.
    """
    text = (raw or "").strip()
    if text == "NONE":
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


async def embed(text: str) -> list[float]:
    """Embed a single string with the configured embedding model."""
    client = get_client()
    async with get_semaphore():
        response = await client.embeddings.create(
            model=settings.embedding_model,
            input=text,
        )
    return response.data[0].embedding


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def parse_json(raw: str) -> Any:
    """
    Best effort JSON parse of a model response.

    Handles fenced code blocks, a literal NONE sentinel, and objects wrapped in
    incidental prose. Returns None when the text cannot be salvaged.
    """
    if not raw:
        return None

    text = _FENCE.sub("", raw.strip())
    if text.strip().upper() == "NONE":
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fall back to the outermost balanced object or array in the response.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    log.warning("Could not parse model response as JSON: %s", text[:200])
    return None


async def probe() -> dict[str, Any]:
    """
    Confirm the key works and that the embedding width matches the stored
    vectors. A mismatch here is the single most common cause of a working
    looking app that returns irrelevant results.
    """
    vector = await embed("dimension probe")
    return {
        "embedding_dimensions": len(vector),
        "matches_index": len(vector) == settings.embedding_dimensions,
    }
