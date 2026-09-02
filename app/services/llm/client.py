"""LLM transport.

Ollama and xAI (Grok) both speak the OpenAI chat-completions wire format, so
there is one client and two configurations rather than two SDKs. That also
keeps the planner testable: it depends on a callable, not on a vendor object.

Nothing here knows what a workflow graph is. Prompt construction lives in
prompt.py and graph repair in planner.py, so a provider change touches only
this file.
"""

from __future__ import annotations

import json
import logging
import time

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """Transport or configuration failure. Never raised for a bad graph --
    an unusable graph is the planner's problem, not the client's."""


class ChatClient:
    """Minimal OpenAI-compatible chat client.

    json_mode asks the server for a JSON object. Ollama honours it only on
    recent builds and Grok honours it properly, so it is a hint, not a
    guarantee -- the planner still extracts JSON defensively.
    """

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 timeout: float = 120.0, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries

    @property
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def complete(self, system: str, user: str, *, temperature: float = 0.0,
                 json_mode: bool = True) -> str:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # A planner is not a place for creativity: the same query should
            # produce the same graph.
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.base_url}/chat/completions"
        delay = 2.0

        for attempt in range(1, self.max_retries + 1):
            try:
                r = httpx.post(url, headers=self._headers, json=payload,
                               timeout=self.timeout)
                if r.status_code in _RETRYABLE_STATUS and attempt < self.max_retries:
                    log.warning("LLM %s returned %s; retrying in %.0fs",
                                url, r.status_code, delay)
                    time.sleep(delay)
                    delay *= 2
                    continue
                r.raise_for_status()
                return _first_message(r.json())

            except httpx.HTTPStatusError as e:
                body = (e.response.text or "")[:500]
                raise LLMError(
                    f"{self.model} at {self.base_url} returned "
                    f"{e.response.status_code}: {body}") from e
            except httpx.RequestError as e:
                if attempt == self.max_retries:
                    raise LLMError(
                        f"Could not reach {self.base_url}: {e}. If this is "
                        f"Ollama, check that `ollama serve` is running."
                    ) from e
                log.warning("LLM request failed (%s); retrying in %.0fs", e, delay)
                time.sleep(delay)
                delay *= 2

        raise LLMError("unreachable")


def _first_message(body: dict) -> str:
    try:
        return body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(
            f"Unexpected response shape: {json.dumps(body)[:500]}") from e


def get_client() -> ChatClient:
    """Build the configured client. Raises rather than silently defaulting --
    a planner that quietly talks to the wrong model is worse than one that
    refuses to start.
    """
    s = get_settings()
    provider = (s.llm_provider or "").lower()

    if provider == "ollama":
        return ChatClient(base_url=s.ollama_base_url, model=s.ollama_model,
                          api_key=None, timeout=s.llm_timeout_seconds)

    if provider in ("grok", "xai"):
        if not s.xai_api_key:
            raise LLMError(
                "LLM_PROVIDER=grok but XAI_API_KEY is empty. Get a key from "
                "https://console.x.ai and set it in .env."
            )
        return ChatClient(base_url=s.xai_base_url, model=s.xai_model,
                          api_key=s.xai_api_key, timeout=s.llm_timeout_seconds)

    raise LLMError(
        f"Unknown LLM_PROVIDER={s.llm_provider!r}. Use 'ollama' or 'grok'.")