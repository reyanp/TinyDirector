"""Thin wrapper around NVIDIA's OpenAI-compatible NIM endpoint for Nemotron.

Config comes ONLY from environment variables (optionally loaded from the
gitignored repo-root .env):
  NVIDIA_API_KEY      required for live calls (an `nvapi-...` key from build.nvidia.com)
  NEMOTRON_MODEL      default nvidia/nemotron-3.5-super-vl-120b-a12b
  NEMOTRON_BASE_URL   default https://integrate.api.nvidia.com/v1 (swap to a Brev NIM URL)
  NEMOTRON_THINKING   "0" (default) or "1" -> chat_template_kwargs.enable_thinking
  NEMOTRON_TIMEOUT    seconds, default 90
The API key is never logged or printed.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Protocol

DEFAULT_MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"


class ConfigError(RuntimeError):
    """Missing key or bad configuration (CLI exit code 2)."""


class ModelCallError(RuntimeError):
    """The endpoint failed (auth, network, timeout, rate limit)."""


@dataclass
class ModelReply:
    content: str
    reasoning: str = ""
    model: str = ""
    latency_s: float = 0.0
    usage: dict = field(default_factory=dict)
    live: bool = True


class ModelClient(Protocol):
    model: str
    live: bool

    def complete(self, messages: list[dict], *, max_tokens: int | None = None) -> ModelReply: ...


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


class NemotronClient:
    live = True

    def __init__(self, model: str | None = None, base_url: str | None = None,
                 thinking: bool | None = None, timeout: float | None = None,
                 temperature: float = 0.2):
        api_key = os.environ.get("NVIDIA_API_KEY", "").strip()
        if not api_key:
            raise ConfigError(
                "NVIDIA_API_KEY is not set. Get an 'nvapi-...' key at https://build.nvidia.com "
                "(open the Nemotron model page -> Get API Key), then put NVIDIA_API_KEY=... in the "
                "repo-root .env file or export it in this terminal. (A Brev 'bak-...' key will not work here.)")
        if api_key.startswith("bak-"):
            raise ConfigError("NVIDIA_API_KEY looks like a Brev CLI key ('bak-...'). The model API needs an "
                              "'nvapi-...' key from build.nvidia.com.")
        from openai import OpenAI  # imported lazily so offline tests don't need network config

        self.model = model or os.environ.get("NEMOTRON_MODEL", DEFAULT_MODEL)
        self.base_url = base_url or os.environ.get("NEMOTRON_BASE_URL", DEFAULT_BASE_URL)
        self.thinking = _truthy(os.environ.get("NEMOTRON_THINKING")) if thinking is None else thinking
        self.timeout = timeout or float(os.environ.get("NEMOTRON_TIMEOUT", "90"))
        self.temperature = temperature
        self._client = OpenAI(base_url=self.base_url, api_key=api_key, timeout=self.timeout, max_retries=0)

    def complete(self, messages: list[dict], *, max_tokens: int | None = None) -> ModelReply:
        max_tokens = max_tokens or (8192 if self.thinking else 2048)
        start = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                top_p=0.95,
                max_tokens=max_tokens,
                extra_body={"chat_template_kwargs": {"enable_thinking": self.thinking}},
            )
        except Exception as exc:  # openai raises typed errors; normalize, never include the key
            status = getattr(exc, "status_code", None)
            hint = {401: "key rejected - check NVIDIA_API_KEY",
                    403: "key not authorized for this model/endpoint",
                    404: f"model {self.model!r} not found at {self.base_url}",
                    429: "rate limited - wait a moment or switch NEMOTRON_BASE_URL to your Brev NIM"}.get(status, "")
            raise ModelCallError(f"{type(exc).__name__}{f' {status}' if status else ''}: {hint or exc}") from None
        latency = time.perf_counter() - start
        choice = resp.choices[0]
        msg = choice.message
        reasoning = getattr(msg, "reasoning_content", None) or ""
        if not reasoning and getattr(msg, "model_extra", None):
            reasoning = msg.model_extra.get("reasoning_content") or ""
        usage = resp.usage.model_dump() if getattr(resp, "usage", None) else {}
        usage["finish_reason"] = choice.finish_reason
        return ModelReply(content=msg.content or "", reasoning=reasoning, model=resp.model or self.model,
                          latency_s=latency, usage=usage, live=True)
