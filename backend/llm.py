"""OpenAI-compatible cloud LLM client for triage field notes.

Text-only by design: the model receives the pipeline's measured metrics as a
JSON blob plus a grounded system prompt, and returns 2-3 sentences. No imagery
is sent, so any chat model works and per-note cost stays minimal.

Configuration is read from the environment (loaded from ``.env`` at import of
the ``backend`` package):

    LLM_API_KEY    bearer token
    LLM_BASE_URL   full endpoint or base URL; normalized to /chat/completions
    LLM_MODEL      model id
    LLM_TIMEOUT    optional, seconds (default 60)
    LLM_TEMPERATURE optional, default 0.2

Everything here fails soft: callers get a typed ``LLMError`` and the vision
layer moves on to the next provider. A missing key simply means "not
configured", which is not an error.
"""
from __future__ import annotations

import os
import re
from typing import Any

import httpx

SYSTEM_PROMPT = (
    "You are assisting disaster-relief triage. You are given measured metrics "
    "for one settlement from a satellite flood-detection pipeline. In 2-3 short "
    "sentences, state how badly the settlement is affected and whether road "
    "access is disrupted, then advise on verification. Use ONLY the numbers "
    "provided; never invent population figures, casualties, or place details. "
    "Be concrete and cautious, and name the settlement. Write plain prose: no "
    "markdown, no bold or italic markers, no bullet points or headings."
)

# Hosts that already carry the API prefix; we only append the path.
_CHAT_PATH = "/chat/completions"

# Conservative markdown unwrapping. Notes are stored, exported to CSV/GeoJSON,
# and rendered as escaped text in the UI, so emphasis markers would show up
# literally. We strip the syntax but never the words.
_MD_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\*\*(.+?)\*\*", re.S), r"\1"),          # **bold**
    (re.compile(r"__(.+?)__", re.S), r"\1"),              # __bold__
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", re.S), r"\1"),  # *italic*
    (re.compile(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", re.S), r"\1"),    # _italic_
    (re.compile(r"`{1,3}(.+?)`{1,3}", re.S), r"\1"),      # `code`
    (re.compile(r"^\s{0,3}#{1,6}\s+", re.M), ""),         # # heading
    (re.compile(r"^\s{0,3}[-*+]\s+", re.M), ""),          # - bullet
]


def to_plain_text(s: str) -> str:
    """Unwrap common markdown syntax into plain prose."""
    for pattern, repl in _MD_RULES:
        s = pattern.sub(repl, s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s*\n\s*", " ", s)
    return s.strip()



class LLMError(RuntimeError):
    """Any failure talking to the configured cloud LLM."""


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def api_key() -> str:
    return _env("LLM_API_KEY")


def base_url() -> str:
    return _env("LLM_BASE_URL")


def model() -> str:
    return _env("LLM_MODEL")


def _timeout() -> httpx.Timeout:
    try:
        t = float(_env("LLM_TIMEOUT") or 60.0)
    except ValueError:
        t = 60.0
    return httpx.Timeout(t, connect=10.0)


def _temperature() -> float:
    try:
        return float(_env("LLM_TEMPERATURE") or 0.2)
    except ValueError:
        return 0.2


def endpoint() -> str:
    """Normalize ``LLM_BASE_URL`` into a concrete chat-completions URL.

    Accepts any of:
        https://host/v1                       -> https://host/v1/chat/completions
        https://host/v1/chat/completions      -> unchanged
        https://host/openai/v1/               -> https://host/openai/v1/chat/completions
        https://host/v1/chat/completions?x=1  -> path appended before the query
    """
    raw = base_url()
    if not raw:
        return ""
    url = raw.rstrip("/")
    if url.endswith(_CHAT_PATH):
        return url
    # Preserve any query string that a gateway may require.
    query = ""
    if "?" in url:
        url, _, q = url.partition("?")
        query = f"?{q}"
        url = url.rstrip("/")
    return f"{url}{_CHAT_PATH}{query}"


def host_label() -> str:
    """Short, non-secret identifier for the configured provider."""
    url = base_url()
    if not url:
        return ""
    try:
        return httpx.URL(url).host or url
    except Exception:  # noqa: BLE001 - never fail on a label
        return url


def configured() -> bool:
    return bool(api_key() and base_url() and model())


def available() -> tuple[bool, str]:
    """Return (ready, detail). Detail names the model or what is missing.

    This is a cheap configuration check, not a network probe, so it is safe to
    call on every status request.
    """
    missing = [n for n, v in (
        ("LLM_API_KEY", api_key()),
        ("LLM_BASE_URL", base_url()),
        ("LLM_MODEL", model()),
    ) if not v]
    if missing:
        return False, f"cloud LLM not configured (missing {', '.join(missing)})"
    return True, model()


def complete(user_prompt: str, system_prompt: str = SYSTEM_PROMPT) -> str:
    """Call the chat-completions endpoint and return the assistant text.

    Raises ``LLMError`` on any problem so the caller can fall through to the
    next provider.
    """
    if not configured():
        raise LLMError("cloud LLM not configured")

    url = endpoint()
    payload: dict[str, Any] = {
        "model": model(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": _temperature(),
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
    }

    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=_timeout())
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        # Surface the provider's message without leaking the key.
        snippet = ""
        try:
            body = e.response.json()
            snippet = (
                body.get("error", {}).get("message")
                if isinstance(body.get("error"), dict) else None
            ) or str(body)[:200]
        except Exception:  # noqa: BLE001
            snippet = (e.response.text or "")[:200]
        raise LLMError(f"HTTP {e.response.status_code}: {snippet}".strip()) from e
    except httpx.HTTPError as e:
        raise LLMError(f"request failed: {e.__class__.__name__}") from e

    try:
        data = r.json()
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise LLMError("unexpected response shape") from e

    if not text:
        raise LLMError("empty completion")
    return to_plain_text(text)
