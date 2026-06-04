"""LM Studio client — OpenAI-compatible interface to a local model server.

Streams responses so the Thought Chamber can render tokens live.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Generator, Iterable

from openai import OpenAI


logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    name: str | None = None

    def to_openai(self) -> dict:
        msg: dict = {"role": self.role, "content": self.content}
        if self.name:
            msg["name"] = self.name
        return msg


@dataclass
class LMStudioConfig:
    base_url: str = "http://localhost:1234/v1"
    api_key: str = "lm-studio"
    model: str = "local-model"
    temperature: float = 0.7
    max_tokens: int = 4096
    request_timeout_seconds: float | None = None  # None = no time limit on generation
    # Reactive pre-flight budget (input side, tokens). When the estimate of the
    # outgoing prompt exceeds this, prompt_preflight.trim_to_fit drops the
    # oldest non-system / non-final-user messages until it fits. Pair with
    # max_tokens so input + output ≤ loaded model context window.
    input_context_tokens: int = 5000

    @classmethod
    def from_dict(cls, d: dict) -> "LMStudioConfig":
        return cls(**{k: d[k] for k in d if k in cls.__annotations__})


@dataclass
class StreamEvent:
    """One delta from the model. `done=True` marks the final event with full text."""
    delta: str = ""
    full_text: str = ""
    done: bool = False
    error: str | None = None
    transient: bool = False  # True if the error is worth reconnect-and-retry


# Exception class names openai (and httpx underneath it) raise when the server
# is down or overloaded. These are recoverable: we pause as resumable, poll
# until LM Studio comes back, then auto-continue.
_TRANSIENT_HINTS = (
    "APIConnectionError",
    "APITimeoutError",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "RemoteProtocolError",
    "InternalServerError",      # 500
    "BadGatewayError",          # 502
    "ServiceUnavailableError",  # 503
    "GatewayTimeoutError",      # 504
)


def is_transient_error(exc: BaseException) -> bool:
    """Classify an exception as transient (worth a reconnect-and-retry) or
    permanent (bad request, auth, etc.)."""
    name = type(exc).__name__
    if any(h in name for h in _TRANSIENT_HINTS):
        return True
    # The openai SDK also surfaces HTTPStatusError with a .response carrying
    # the status code. >=500 is recoverable; <500 isn't.
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int) and status >= 500:
        return True
    # Cause chain — sometimes openai wraps an httpx error we'd recognise.
    cause = exc.__cause__ or exc.__context__
    if cause and cause is not exc:
        return is_transient_error(cause)
    return False


class LMStudioClient:
    def __init__(self, config: LMStudioConfig):
        self.config = config
        self._client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=config.request_timeout_seconds,
            max_retries=0,  # fail fast when the server is down (UI polls on its own)
        )
        self._resolved_model: str | None = None

    # ----- model discovery -----------------------------------------------

    PLACEHOLDER_MODELS = {"local-model", "auto", "", None}

    def list_models(self, *, timeout: float = 4.0) -> list[str]:
        models = self._client.with_options(timeout=timeout).models.list()
        return [m.id for m in models.data] if models.data else []

    def resolve_model(self, *, timeout: float = 4.0) -> str:
        """Return the model id to send. If config names a real model, trust it;
        otherwise query the server and cache the first chat-capable model."""
        if self.config.model not in self.PLACEHOLDER_MODELS:
            return self.config.model
        if self._resolved_model:
            return self._resolved_model
        try:
            ids = self.list_models(timeout=timeout)
            chat_ids = [i for i in ids if "embed" not in i.lower()]
            self._resolved_model = (chat_ids or ids or ["local-model"])[0]
        except Exception:  # noqa: BLE001
            self._resolved_model = self.config.model or "local-model"
        return self._resolved_model

    @property
    def active_model(self) -> str:
        return self._resolved_model or self.config.model

    def health_check(self) -> tuple[bool, str]:
        """Fast probe so the UI never blocks on a dead server. 3.5s tolerates a
        momentarily-busy server (e.g. mid-generation) without flicking to OFFLINE,
        while a truly-down server still fails fast (connection refused is instant)."""
        try:
            ids = self.list_models(timeout=3.5)
            if not ids:
                return True, "OK — connected, but NO model is loaded. Load one in LM Studio."
            chat_ids = [i for i in ids if "embed" not in i.lower()]
            self._resolved_model = (chat_ids or ids)[0]
            return True, f"OK — model: {self._resolved_model}"
        except Exception as exc:  # noqa: BLE001
            return False, f"offline ({type(exc).__name__})"

    def stream_chat(
        self,
        messages: Iterable[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Generator[StreamEvent, None, None]:
        # Pre-flight: cap the input side against the configured budget. The
        # trimmer is a no-op when the estimate fits — for normal turns this
        # adds a few microseconds of math and zero context loss. When it does
        # trim, the model never sees the dropped messages, so the request
        # can't overflow the loaded model's context window.
        from .prompt_preflight import trim_to_fit  # local import keeps api_client cycle-free
        message_list = list(messages)
        budget = getattr(self.config, "input_context_tokens", 5000)
        trimmed, report = trim_to_fit(message_list, input_budget=budget)
        if report.trimmed:
            logger.warning(
                "Prompt pre-flight dropped %d message(s) (%d → %d est. tokens, budget %d).",
                report.dropped_messages, report.estimated_before, report.estimated_after, budget,
            )
        payload = [m.to_openai() for m in trimmed]
        try:
            stream = self._client.chat.completions.create(
                model=self.resolve_model(),
                messages=payload,
                temperature=temperature if temperature is not None else self.config.temperature,
                max_tokens=max_tokens or self.config.max_tokens,
                stream=True,
            )
            full = []
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                token = (delta.content or "") if delta else ""
                if not token:
                    continue
                full.append(token)
                yield StreamEvent(delta=token, full_text="".join(full))
            yield StreamEvent(full_text="".join(full), done=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("LM Studio stream failed")
            yield StreamEvent(
                done=True,
                error=str(exc),
                transient=is_transient_error(exc),
            )

    def complete(self, messages: Iterable[ChatMessage], **kwargs) -> str:
        """Non-streaming convenience wrapper."""
        text = ""
        for ev in self.stream_chat(messages, **kwargs):
            if ev.error:
                raise RuntimeError(ev.error)
            if ev.done:
                text = ev.full_text
        return text
