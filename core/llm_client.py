"""
Local LLM client for MARK XL.

Supports two backends — selected via  "llm_provider"  in config/api_keys.json:

  "llm_provider": "ollama"   (default)
        Uses Ollama's native /api/chat endpoint.
        Download: https://ollama.com
        Default port: 11434

  "llm_provider": "openai"
        Uses any OpenAI-compatible server: LM Studio, Jan, LocalAI,
        llama.cpp server, vLLM, etc.
        LM Studio download: https://lmstudio.ai   (default port: 1234)
        Set  "llm_url": "http://localhost:1234"  in config.
        Note: tool-calling support depends on the model; use a model that
        supports function/tool calls (e.g. Qwen2.5, Llama-3.1, Mistral).
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Generator

import requests

# Matches a sentence boundary: [.!?] followed by whitespace, or a blank line.
# Avoids splitting on decimals (3.5) because those have no space after the dot.
_SENT_END = re.compile(r'(?<=[.!?])\s+|(?<=\n)\s*\n')

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR    = get_base_dir()
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

_DEFAULTS = {
    "llm_url":      "http://localhost:11434",
    "llm_model":    "llama3.2",
    "llm_provider": "ollama",   # "ollama" | "openai" | "groq" | "claude"
}

_CLAUDE_API_BASE = "https://api.anthropic.com/v1"
_CLAUDE_DEFAULT_MODEL = "claude-3-5-sonnet-20241022"
_ANTHROPIC_VERSION = "2023-06-01"


def get_llm_provider() -> str:
    """Returns 'ollama', 'openai', 'groq', or 'claude'."""
    raw = _load_config().get("llm_provider", "ollama").strip().lower()
    if raw in ("claude", "anthropic"):
        return "claude"
    if raw == "groq":
        return "groq"
    return "openai" if raw in ("openai", "lmstudio", "localai", "jan", "llamacpp") else "ollama"


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_llm_api_key() -> str:
    """Returns the API key for the configured LLM provider."""
    cfg = _load_config()
    provider = get_llm_provider()
    if provider == "groq":
        return cfg.get("groq_api_key") or os.environ.get("GROQ_API_KEY", "")
    if provider == "claude":
        return cfg.get("claude_api_key") or cfg.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    return cfg.get("openai_api_key") or cfg.get("llm_api_key") or os.environ.get("OPENAI_API_KEY", "")


def _get_headers() -> dict:
    key = get_llm_api_key()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def ensure_ollama_running(timeout: int = 15) -> bool:
    """
    For Ollama: ping /api/tags; auto-launch 'ollama serve' if not running.
    For OpenAI/Groq compatible providers: just ping /v1/models (server must be started manually).
    Returns True if the LLM server is reachable.
    """
    url, _   = get_llm_settings()
    provider = get_llm_provider()

    if provider in ("openai", "groq"):
        # OpenAI/Groq compatible servers
        health = f"{url}/v1/models"
        try:
            ok = requests.get(health, headers=_get_headers(), timeout=5).status_code == 200
            if ok:
                print(f"[LLM] {provider.upper()} server reachable at {url}")
            else:
                print(f"[LLM] Server at {url} returned non-200. Is the API key valid?")
            return ok
        except Exception as e:
            print(
                f"[LLM] Cannot reach {provider.upper()} server at {url}.\n"
                f"      Details: {e}"
            )
            return False

    if provider == "claude":
        # Ping Anthropic API with a minimal request
        api_key = get_llm_api_key()
        if not api_key:
            print("[LLM] Claude API key not configured. Set 'claude_api_key' in config/api_keys.json")
            return False
        try:
            resp = requests.post(
                f"{_CLAUDE_API_BASE}/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": _ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json={"model": _CLAUDE_DEFAULT_MODEL, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
                timeout=8,
            )
            if resp.status_code in (200, 400):  # 400 = bad request but server is up
                print("[LLM] Claude (Anthropic) API reachable.")
                return True
            print(f"[LLM] Claude API returned {resp.status_code}: {resp.text[:200]}")
            return False
        except Exception as e:
            print(f"[LLM] Cannot reach Claude API: {e}")
            return False

    # ── Ollama ──────────────────────────────────────────────────────────────
    health = f"{url}/api/tags"

    def _is_up() -> bool:
        try:
            return requests.get(health, timeout=3).status_code == 200
        except Exception:
            return False

    if _is_up():
        return True

    print("[LLM] Ollama not running — launching 'ollama serve'…")
    try:
        kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(["ollama", "serve"], **kwargs)
    except FileNotFoundError:
        print("[LLM] 'ollama' command not found. Install Ollama from https://ollama.com")
        return False
    except Exception as e:
        print(f"[LLM] Could not launch Ollama: {e}")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.0)
        if _is_up():
            print("[LLM] Ollama started successfully.")
            return True

    print("[LLM] Ollama did not respond within the timeout.")
    return False


def warmup_model(system_prompt: str | None = None) -> bool:
    """
    Pre-load the model AND prime Ollama's KV prefix cache.

    Why the system_prompt matters
    ─────────────────────────────
    Ollama caches the KV attention state of the prompt prefix across requests.
    If warmup includes the same system prompt that real requests will use, Ollama
    evaluates those tokens ONCE at startup.  Every subsequent request only needs
    to evaluate the small delta (user message ± time context) instead of the full
    300-500 token system prompt → drops first-token latency from ~17 s to <1 s.

    Pass the *static* part of the system prompt (the JARVIS protocol text, without
    timestamps or per-minute context) so the prefix stays valid across calls.
    """
    url, model = get_llm_settings()
    provider   = get_llm_provider()
    print(f"[LLM] Warming up '{model}' ({provider})…")

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": "hi"})

    if provider in ("openai", "groq"):
        # OpenAI/Groq compatible: just fire a minimal request to ensure the model is loaded.
        # No keep_alive or KV-cache priming available — server manages this internally.
        payload = {
            "model":      model,
            "messages":   messages,
            "stream":     False,
            "max_tokens": 1,
        }
        try:
            resp = requests.post(f"{url}/v1/chat/completions", json=payload, headers=_get_headers(), timeout=180)
            resp.raise_for_status()
            print(f"[LLM] '{model}' ready ({provider.upper()}).")
            return True
        except Exception as e:
            print(f"[LLM] Warmup failed (non-fatal): {e}")
            return False

    # ── Ollama ──────────────────────────────────────────────────────────────
    payload = {
        "model":      model,
        "messages":   messages,
        "stream":     False,
        "keep_alive": -1,
        # num_gpu:99 → push ALL transformer layers to GPU (Ollama caps at available)
        # This is safe even without a GPU — Ollama silently ignores if n_gpu_layers=0
        "options":    {"num_predict": 1, "num_gpu": 99},
    }
    try:
        resp = requests.post(f"{url}/api/chat", json=payload, timeout=180)
        resp.raise_for_status()
        print(f"[LLM] '{model}' loaded and KV cache primed.")
        return True
    except Exception as e:
        print(f"[LLM] Warmup failed (non-fatal): {e}")
        return False


def check_model_available(log: Callable | None = None) -> bool:
    """
    Returns True if the configured model is already pulled in Ollama.
    Logs an actionable warning (to console + optional UI callback) if not.
    Always returns True for non-Ollama providers (cannot inspect their model list).
    """
    if get_llm_provider() != "ollama":
        return True

    url, model = get_llm_settings()
    try:
        resp = requests.get(f"{url}/api/tags", timeout=5)
        resp.raise_for_status()
        pulled = [m.get("name", "") for m in resp.json().get("models", [])]
        model_base = model.split(":")[0]
        found = any(
            m == model or m == f"{model}:latest" or m == model_base or m.startswith(model_base + ":")
            for m in pulled
        )
        if not found:
            available = ", ".join(pulled) if pulled else "none"
            warn = (
                f"WRN: Model '{model}' is not pulled in Ollama.\n"
                f"     Available: {available}\n"
                f"     Fix: ollama pull {model}"
            )
            print(warn)
            if log:
                log(f"WRN: '{model}' not found — run: ollama pull {model}")
        return found
    except Exception:
        return True   # Ollama might still be starting up; non-blocking


def check_llm_readiness(auto_pull: bool = True) -> tuple[bool, str]:
    """
    Diagnostic & readiness check for LLM backend.
    Logs:
      [LLM] Ollama URL: ...
      [LLM] Ollama server: READY/NOT READY
      [LLM] Available models: ...
      [LLM] Selected model: ...
    Verifies that the configured model is available locally.
    """
    url, model = get_llm_settings()
    provider = get_llm_provider()

    print(f"[LLM] Ollama URL: {url}")

    if not ensure_ollama_running(timeout=15):
        print("[LLM] Ollama server: NOT READY")
        return False, f"Ollama server is not reachable at {url}"

    print("[LLM] Ollama server: READY")

    if provider == "ollama":
        try:
            resp = requests.get(f"{url}/api/tags", timeout=5)
            if resp.status_code == 200:
                raw_models = resp.json().get("models", [])
                available = [m.get("name", "") for m in raw_models]
                avail_str = ", ".join(available) if available else "None"
                print(f"[LLM] Available models: {avail_str}")
                print(f"[LLM] Selected model: {model}")

                model_base = model.split(":")[0]
                model_found = any(
                    m == model or m == f"{model}:latest" or m == model_base or m.startswith(f"{model_base}:")
                    for m in available
                )
                if not model_found:
                    print(f"[LLM] Model '{model}' not found in local Ollama repository.")
                    if auto_pull:
                        print(f"[LLM] Attempting to pull '{model}'...")
                        res = subprocess.run(["ollama", "pull", model], capture_output=True, text=True)
                        if res.returncode == 0:
                            print(f"[LLM] Model '{model}' pulled successfully.")
                            return True, f"Model '{model}' ready"
                        else:
                            print(f"[LLM] Failed to pull '{model}': {res.stderr}")
                    return False, f"Model '{model}' is not pulled. Run: ollama pull {model}"
                return True, f"Model '{model}' ready"
        except Exception as e:
            print(f"[LLM] Error querying /api/tags: {e}")
            return False, str(e)

    return True, "LLM server ready"


def get_llm_settings() -> tuple[str, str]:
    """Returns (base_url, model_name)."""
    cfg      = _load_config()
    provider = get_llm_provider()
    if provider == "groq":
        url   = cfg.get("llm_url", "https://api.groq.com/openai").rstrip("/")
        model = cfg.get("llm_model", "qwen/qwen3.8-27b")
        return url, model
    if provider == "claude":
        url   = _CLAUDE_API_BASE
        model = cfg.get("llm_model", _CLAUDE_DEFAULT_MODEL)
        return url, model
    url   = cfg.get("llm_url",   _DEFAULTS["llm_url"]).rstrip("/")
    model = cfg.get("llm_model", _DEFAULTS["llm_model"])
    return url, model


def call_llm(
    messages: list,
    tools:    list | None = None,
    timeout:  int = 120,
) -> dict:
    """
    Non-streaming chat request.  Routes to Ollama or OpenAI/Groq compatible backend.

    Returns:
        {"content": str, "tool_calls": list}
    """
    url, model = get_llm_settings()
    provider   = get_llm_provider()

    if provider == "claude":
        return _call_llm_claude(messages, tools, timeout)

    if provider in ("openai", "groq"):
        endpoint = f"{url}/v1/chat/completions"
        payload: dict = {
            "model":      model,
            "messages":   messages,
            "stream":     False,
            "max_tokens": 150,
        }
        if tools:
            payload["tools"]       = tools
            payload["tool_choice"] = "auto"
        try:
            resp = requests.post(endpoint, json=payload, headers=_get_headers(), timeout=timeout)
            resp.raise_for_status()
            choice = resp.json().get("choices", [{}])[0]
            msg    = choice.get("message", {})
            # OpenAI tool_calls format → normalise to Ollama-style
            raw_tc  = msg.get("tool_calls") or []
            tc_list = [
                {
                    "id":       t.get("id", ""),
                    "function": {
                        "name":      t["function"]["name"],
                        "arguments": (
                            json.loads(t["function"]["arguments"])
                            if isinstance(t["function"].get("arguments"), str)
                            else t["function"].get("arguments", {})
                        ),
                    },
                }
                for t in raw_tc
            ]
            return {
                "content":    (msg.get("content") or "").strip(),
                "tool_calls": tc_list,
            }
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            _is_transient = status in (429, 503) or any(
                k in str(e).lower() for k in ("rate", "quota", "unavailable", "high demand", "overloaded")
            )
            if _is_transient:
                print(f"[LLM] {provider.upper()} {status} - falling back to Ollama llama3.2, Sir...")
                return _ollama_fallback_call(messages, tools, timeout)
            raise RuntimeError(f"{provider.upper()} LLM call failed: {e}")
        except Exception as e:
            raise RuntimeError(f"{provider.upper()} LLM call failed: {e}")

    # ── Ollama ──────────────────────────────────────────────────────────────
    endpoint = f"{url}/api/chat"
    payload = {
        "model":      model,
        "messages":   messages,
        "stream":     False,
        "keep_alive": -1,
        "options":    {"num_predict": 150, "num_gpu": 99},
    }
    if tools:
        payload["tools"] = tools

    try:
        resp = requests.post(endpoint, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        msg  = data.get("message", {})
        return {
            "content":    (msg.get("content") or "").strip(),
            "tool_calls": msg.get("tool_calls") or [],
        }
    except requests.exceptions.ConnectionError as e:
        print(f"[LLM] ConnectionError — trying to restart Ollama… ({e})")
        if ensure_ollama_running():
            try:
                resp = requests.post(endpoint, json=payload, timeout=timeout)
                resp.raise_for_status()
                data = resp.json()
                msg  = data.get("message", {})
                return {
                    "content":    (msg.get("content") or "").strip(),
                    "tool_calls": msg.get("tool_calls") or [],
                }
            except Exception:
                pass
        raise RuntimeError(
            f"Cannot connect to Ollama at {url}. "
            "Make sure Ollama is installed and run: ollama serve"
        )
    except requests.exceptions.Timeout:
        raise RuntimeError("Ollama request timed out after 120 s.")
    except requests.exceptions.HTTPError as e:
        err_detail = ""
        try:
            err_detail = e.response.json().get("error", "")
        except Exception:
            err_detail = e.response.text if e.response is not None else ""
        if e.response is not None and e.response.status_code == 404:
            print(f"[LLM] HTTP 404 Not Found from {endpoint}: {err_detail or 'Model or endpoint not found'}")
            raise RuntimeError(f"Ollama HTTP 404 Not Found: {err_detail or f'Model {model} not found'}")
        print(f"[LLM] HTTPError: {e.response.status_code if e.response else 'unknown'} — {err_detail[:200]}")
        raise RuntimeError(f"Ollama HTTP error {e.response.status_code if e.response else 'unknown'}: {err_detail or e}")
    except Exception as e:
        print(f"[LLM] Unexpected error: {type(e).__name__}: {e}")
        raise RuntimeError(f"LLM call failed: {e}")


def _call_llm_claude(
    messages: list,
    tools:    list | None,
    timeout:  int,
) -> dict:
    """
    Non-streaming Claude (Anthropic Messages API) call.
    Converts OpenAI-style tool definitions to Anthropic format.
    """
    _, model = get_llm_settings()
    api_key  = get_llm_api_key()
    if not api_key:
        raise RuntimeError("Claude API key not configured. Set 'claude_api_key' in config/api_keys.json")

    headers = {
        "x-api-key":         api_key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type":      "application/json",
    }

    # Separate system messages from conversation
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    conv_messages = [m for m in messages if m.get("role") != "system"]
    system_text   = "\n\n".join(system_parts) if system_parts else None

    payload: dict = {
        "model":      model,
        "max_tokens": 1024,
        "messages":   conv_messages,
    }
    if system_text:
        payload["system"] = system_text

    # Convert OpenAI-style tools → Anthropic tools format
    if tools:
        anthropic_tools = []
        for t in tools:
            fn = t.get("function", t)
            anthropic_tools.append({
                "name":         fn.get("name", ""),
                "description":  fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        payload["tools"] = anthropic_tools

    try:
        resp = requests.post(f"{_CLAUDE_API_BASE}/messages", json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        content_blocks = data.get("content", [])
        text_content   = " ".join(b.get("text", "") for b in content_blocks if b.get("type") == "text").strip()
        tool_calls     = [
            {
                "id":       b.get("id", ""),
                "function": {"name": b.get("name", ""), "arguments": b.get("input", {})},
            }
            for b in content_blocks if b.get("type") == "tool_use"
        ]
        return {"content": text_content, "tool_calls": tool_calls}

    except Exception as e:
        raise RuntimeError(f"Claude LLM call failed: {e}")


def _stream_claude(
    messages: list,
    tools:    list | None,
    timeout:  int,
) -> Generator[dict, None, None]:
    """
    Streaming Claude (Anthropic) backend.
    Parses Anthropic SSE stream and yields sentence/done events
    matching the same output format as _stream_openai().
    """
    _, model = get_llm_settings()
    api_key  = get_llm_api_key()
    if not api_key:
        raise RuntimeError("Claude API key not configured. Set 'claude_api_key' in config/api_keys.json")

    headers = {
        "x-api-key":         api_key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type":      "application/json",
    }

    # Separate system messages
    system_parts  = [m["content"] for m in messages if m.get("role") == "system"]
    conv_messages = [m for m in messages if m.get("role") != "system"]
    system_text   = "\n\n".join(system_parts) if system_parts else None

    payload: dict = {
        "model":      model,
        "max_tokens": 1024,
        "stream":     True,
        "messages":   conv_messages,
    }
    if system_text:
        payload["system"] = system_text
    if tools:
        anthropic_tools = []
        for t in tools:
            fn = t.get("function", t)
            anthropic_tools.append({
                "name":         fn.get("name", ""),
                "description":  fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        payload["tools"] = anthropic_tools

    try:
        with requests.post(
            f"{_CLAUDE_API_BASE}/messages",
            json=payload, headers=headers, timeout=timeout, stream=True
        ) as resp:
            resp.raise_for_status()
            full_content = ""
            buf          = ""
            tool_calls:  list = []
            # Accumulate tool-use blocks by index
            tc_blocks:   dict = {}  # index -> {"id", "name", "input_str"}

            for raw in resp.iter_lines():
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data in ("[DONE]", ""):
                    break

                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                # Text delta
                if event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        full_content += text
                        buf          += text
                        # Flush complete sentences for streaming TTS
                        while True:
                            m = _SENT_END.search(buf)
                            if not m:
                                break
                            sentence = buf[: m.start() + 1].strip()
                            buf      = buf[m.end():]
                            if sentence:
                                yield {"type": "sentence", "text": sentence}
                    elif delta.get("type") == "input_json_delta":
                        idx = event.get("index", 0)
                        if idx in tc_blocks:
                            tc_blocks[idx]["input_str"] += delta.get("partial_json", "")

                elif event_type == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        idx = event.get("index", 0)
                        tc_blocks[idx] = {
                            "id":        block.get("id", ""),
                            "name":      block.get("name", ""),
                            "input_str": "",
                        }

                elif event_type == "message_stop":
                    break

            # Flush trailing buffer
            if buf.strip():
                yield {"type": "sentence", "text": buf.strip()}

            # Parse tool-call blocks
            for idx in sorted(tc_blocks):
                blk = tc_blocks[idx]
                try:
                    args = json.loads(blk["input_str"]) if blk["input_str"] else {}
                except Exception:
                    args = blk["input_str"]
                tool_calls.append({
                    "id":       blk["id"],
                    "function": {"name": blk["name"], "arguments": args},
                })

            yield {
                "type":       "done",
                "content":    full_content.strip(),
                "tool_calls": tool_calls,
            }

    except requests.exceptions.ConnectionError:
        raise RuntimeError("Cannot reach Claude API. Check your internet connection.")
    except requests.exceptions.Timeout:
        raise RuntimeError("Claude stream timed out.")
    except requests.exceptions.HTTPError as e:
        err_detail = ""
        try:
            err_detail = e.response.json().get("error", {}).get("message", "")
        except Exception:
            err_detail = e.response.text if e.response is not None else ""
        raise RuntimeError(f"Claude HTTP error {e.response.status_code if e.response else 'unknown'}: {err_detail or e}")
    except Exception as e:
        raise RuntimeError(f"Claude stream failed: {e}")


def _ensure_local_ollama_running(timeout: int = 15) -> bool:
    """Ensure local Ollama service specifically is reachable at http://localhost:11434."""
    health = "http://localhost:11434/api/tags"

    def _is_up() -> bool:
        try:
            return requests.get(health, timeout=3).status_code == 200
        except Exception:
            return False

    if _is_up():
        return True

    print("[LLM] Ollama not running - launching 'ollama serve'...")
    try:
        kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        ollama_bin = "ollama"
        local_exe = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        if os.path.exists(local_exe):
            ollama_bin = local_exe

        subprocess.Popen([ollama_bin, "serve"], **kwargs)
    except Exception as e:
        print(f"[LLM] Could not launch Ollama: {e}")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.0)
        if _is_up():
            print("[LLM] Ollama started successfully.")
            return True

    print("[LLM] Ollama did not respond within the timeout.")
    return False


def _ollama_fallback_call(
    messages: list,
    tools:    list | None = None,
    timeout:  int = 120,
) -> dict:
    """
    Emergency Ollama llama3.2 fallback for when cloud providers (Groq/Gemini)
    return 429/503. Starts Ollama if not already running.
    """
    ollama_url   = "http://localhost:11434"
    ollama_model = "llama3.2"
    print(f"[LLM] [FALLBACK] Ollama fallback active - using {ollama_model}")
    if not _ensure_local_ollama_running():
        raise RuntimeError(
            "Cloud LLM returned 503/429 AND Ollama is not available. "
            "Install Ollama from https://ollama.com and run: ollama pull llama3.2"
        )
    endpoint = f"{ollama_url}/api/chat"
    payload: dict = {
        "model":      ollama_model,
        "messages":   messages,
        "stream":     False,
        "keep_alive": -1,
        "options":    {"num_predict": 150, "num_gpu": 99},
    }
    if tools:
        payload["tools"] = tools
    resp = requests.post(endpoint, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    msg  = data.get("message", {})
    return {
        "content":    (msg.get("content") or "").strip(),
        "tool_calls": msg.get("tool_calls") or [],
    }


def call_llm_text(
    prompt:  str,
    system:  str | None = None,
    model:   str | None = None,
    timeout: int = 120,
) -> str:
    """
    Simple text-only generation (no tools).
    Used by planner, executor, error_handler, code_helper, dev_agent.
    """
    url, default_model = get_llm_settings()
    provider = get_llm_provider()
    m        = model or default_model

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    if provider == "claude":
        result = _call_llm_claude(messages, None, timeout)
        return result.get("content", "")

    if provider in ("openai", "groq"):
        endpoint = f"{url}/v1/chat/completions"
        payload = {"model": m, "messages": messages, "stream": False, "max_tokens": 600}
        try:
            resp = requests.post(endpoint, json=payload, headers=_get_headers(), timeout=timeout)
            resp.raise_for_status()
            choice = resp.json().get("choices", [{}])[0]
            return (choice.get("message", {}).get("content") or "").strip()
        except Exception as e:
            raise RuntimeError(f"{provider.upper()} text call failed: {e}")

    endpoint = f"{url}/api/chat"
    payload = {"model": m, "messages": messages, "stream": False, "keep_alive": -1, "options": {"num_predict": 600}}

    try:
        resp = requests.post(endpoint, json=payload, timeout=timeout)
        resp.raise_for_status()
        return (resp.json().get("message", {}).get("content") or "").strip()
    except requests.exceptions.ConnectionError:
        if ensure_ollama_running():
            try:
                resp = requests.post(endpoint, json=payload, timeout=timeout)
                resp.raise_for_status()
                return (resp.json().get("message", {}).get("content") or "").strip()
            except Exception:
                pass
        raise RuntimeError(
            f"Cannot connect to Ollama at {url}. "
            "Make sure Ollama is installed and run: ollama serve"
        )
    except Exception as e:
        raise RuntimeError(f"LLM text call failed: {e}")


def _stream_openai(
    messages: list,
    tools:    list | None,
    timeout:  int,
) -> Generator[dict, None, None]:
    """
    Streaming backend for OpenAI-compatible servers (LM Studio, LocalAI, Jan…).

    Parses Server-Sent Events (SSE) and accumulates streaming tool-call fragments
    so the output format is identical to the Ollama backend.
    """
    url, model = get_llm_settings()
    endpoint   = f"{url}/v1/chat/completions"

    payload: dict = {
        "model":      model,
        "messages":   messages,
        "stream":     True,
        "max_tokens": 150,
    }
    if tools:
        payload["tools"]       = tools
        payload["tool_choice"] = "auto"

    try:
        with requests.post(endpoint, json=payload, headers=_get_headers(), timeout=timeout, stream=True) as resp:
            resp.raise_for_status()
            full_content = ""
            buf          = ""
            # tool_call fragments: index → {"id", "function": {"name", "arguments"}}
            tc_fragments: dict[int, dict] = {}

            for raw in resp.iter_lines():
                if not raw:
                    continue
                # SSE lines look like: b"data: {...}" or b"data: [DONE]"
                line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                choice = chunk.get("choices", [{}])[0]
                delta  = choice.get("delta", {})
                text   = delta.get("content") or ""

                full_content += text
                buf          += text

                # Accumulate sentence boundaries for streaming TTS
                while True:
                    m = _SENT_END.search(buf)
                    if not m:
                        break
                    sentence = buf[: m.start() + 1].strip()
                    buf      = buf[m.end():]
                    if sentence:
                        yield {"type": "sentence", "text": sentence}

                # Accumulate streaming tool-call fragments
                for tc in (delta.get("tool_calls") or []):
                    idx = tc.get("index", 0)
                    if idx not in tc_fragments:
                        tc_fragments[idx] = {"id": "", "function": {"name": "", "arguments": ""}}
                    frag = tc_fragments[idx]
                    frag["id"] = frag["id"] or tc.get("id", "")
                    fn = tc.get("function", {})
                    frag["function"]["name"]      += fn.get("name") or ""
                    frag["function"]["arguments"] += fn.get("arguments") or ""

                finish = choice.get("finish_reason")
                if finish in ("stop", "tool_calls", "length"):
                    break

            # Flush any trailing content
            if buf.strip():
                yield {"type": "sentence", "text": buf.strip()}

            # Parse accumulated tool-call argument strings → dicts
            tool_calls: list = []
            for idx in sorted(tc_fragments):
                frag = tc_fragments[idx]
                args = frag["function"]["arguments"]
                try:
                    args = json.loads(args)
                except Exception:
                    pass   # leave as raw string; _execute_tool handles it
                tool_calls.append({
                    "id":       frag["id"],
                    "function": {"name": frag["function"]["name"], "arguments": args},
                })

            yield {
                "type":       "done",
                "content":    full_content.strip(),
                "tool_calls": tool_calls,
            }

    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Cannot reach OpenAI-compatible server at {url}.\n"
            "Make sure LM Studio / LocalAI / Jan is running and the server is started."
        )
    except requests.exceptions.Timeout:
        raise RuntimeError("OpenAI-compatible stream timed out.")
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 0
        _is_transient = status in (429, 503) or any(
            k in str(e).lower() for k in ("rate", "quota", "unavailable", "high demand", "overloaded")
        )
        if _is_transient:
            print(f"[LLM] Groq/OpenAI stream {status} - falling back to Ollama llama3.2 stream, Sir...")
            # Yield from Ollama stream as fallback
            fallback = _ollama_fallback_call(messages, tools, timeout)
            yield {"type": "sentence", "text": fallback["content"]}
            yield {"type": "done", "content": fallback["content"], "tool_calls": fallback["tool_calls"]}
            return
        raise RuntimeError(f"OpenAI-compatible HTTP error: {e.response.status_code}")
    except Exception as e:
        raise RuntimeError(f"OpenAI-compatible stream failed: {e}")


def call_llm_stream(
    messages: list | str,
    tools:    list | None = None,
    timeout:  int = 120,
    system_prompt: str | None = None,
) -> Generator[dict, None, None]:
    """
    Streaming chat request.  Routes to Ollama or OpenAI/Groq compatible backend.

    Yields:
        {"type": "sentence", "text": str}   — each complete sentence as it arrives
        {"type": "done", "content": str, "tool_calls": list}  — when stream ends

    Sentences are split on [.!?] + whitespace so TTS can start immediately.
    Tool calls always appear in the final "done" event.
    """
    if isinstance(messages, str):
        msg_list = []
        if system_prompt:
            msg_list.append({"role": "system", "content": system_prompt})
        msg_list.append({"role": "user", "content": messages})
        messages = msg_list

    provider = get_llm_provider()
    if provider == "claude":
        yield from _stream_claude(messages, tools, timeout)
        return
    if provider in ("openai", "groq"):
        yield from _stream_openai(messages, tools, timeout)
        return

    url, model = get_llm_settings()
    endpoint   = f"{url}/api/chat"

    payload: dict = {
        "model":      model,
        "messages":   messages,
        "stream":     True,
        "keep_alive": -1,
        # 150 tokens ≈ 100 words ≈ 3-4 sentences — enough for any voice reply.
        # num_gpu:99 pushes all layers to GPU; num_thread removed (Ollama auto-tunes).
        "options":    {"num_predict": 150, "num_gpu": 99},
    }
    if tools:
        payload["tools"] = tools

    def _do_stream() -> Generator[dict, None, None]:
        with requests.post(endpoint, json=payload, timeout=timeout, stream=True) as resp:
            resp.raise_for_status()
            full_content = ""
            tool_calls:  list = []
            buf          = ""

            for raw in resp.iter_lines():
                if not raw:
                    continue
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg   = chunk.get("message", {})
                delta = msg.get("content") or ""

                full_content += delta
                buf          += delta

                # Yield complete sentences as they accumulate
                while True:
                    m = _SENT_END.search(buf)
                    if not m:
                        break
                    sentence = buf[: m.start() + 1].strip()
                    buf      = buf[m.end() :]
                    if sentence:
                        yield {"type": "sentence", "text": sentence}

                tc = msg.get("tool_calls")
                if tc:
                    tool_calls.extend(tc)

                if chunk.get("done"):
                    if buf.strip():
                        yield {"type": "sentence", "text": buf.strip()}

                    yield {
                        "type":       "done",
                        "content":    full_content.strip(),
                        "tool_calls": tool_calls,
                    }
                    return

    try:
        yield from _do_stream()
    except requests.exceptions.ConnectionError as e:
        print(f"[LLM] Stream ConnectionError — trying to restart Ollama… ({e})")
        if ensure_ollama_running():
            yield from _do_stream()
            return
        raise RuntimeError(
            f"Cannot connect to Ollama at {url}. "
            "Make sure Ollama is installed and run: ollama serve"
        )
    except requests.exceptions.Timeout:
        raise RuntimeError("Ollama stream timed out.")
    except requests.exceptions.HTTPError as e:
        err_detail = ""
        try:
            err_detail = e.response.json().get("error", "")
        except Exception:
            err_detail = e.response.text if e.response is not None else ""
        if e.response is not None and e.response.status_code == 404:
            print(f"[LLM] HTTP 404 Not Found from {endpoint}: {err_detail or 'Model or endpoint not found'}")
            raise RuntimeError(f"Ollama HTTP 404 Not Found: {err_detail or f'Model {model} not found'}")
        print(f"[LLM] HTTPError: {e.response.status_code if e.response else 'unknown'} — {err_detail[:200]}")
        raise RuntimeError(f"Ollama HTTP error {e.response.status_code if e.response else 'unknown'}: {err_detail or e}")
    except Exception as e:
        print(f"[LLM] Stream error: {type(e).__name__}: {e}")
        raise RuntimeError(f"LLM stream failed: {e}")
