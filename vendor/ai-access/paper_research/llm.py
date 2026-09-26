# -*- coding: utf-8 -*-
"""LLM model provider registry — editable via CLI.

Providers live in config.json → llm.providers. Types:
  openai     — OpenAI-compatible /v1/chat/completions (OpenAI, DeepSeek, Ollama, vLLM…)
  anthropic  — Anthropic Messages API

API keys: prefer environment variable (api_key_env); optional inline api_key for local only.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from .config import CONFIG_PATH, load_config, reload_config


def get_llm_cfg(cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    return cfg.setdefault("llm", {"active_provider": None, "active_model": None, "providers": {}})


def save_cfg(cfg: dict, path: Path | None = None) -> Path:
    p = path or CONFIG_PATH
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reload_config()
    return p


def list_providers(cfg: dict | None = None) -> list[dict]:
    llm = get_llm_cfg(cfg)
    providers = llm.get("providers") or {}
    out = []
    for name, p in providers.items():
        out.append({
            "name": name,
            "label": p.get("label") or name,
            "type": p.get("type", "openai"),
            "base_url": p.get("base_url", ""),
            "enabled": bool(p.get("enabled", True)),
            "models": list(p.get("models") or []),
            "api_key_env": p.get("api_key_env", ""),
            "has_inline_key": bool(p.get("api_key")),
            "active": name == llm.get("active_provider"),
        })
    return out


def resolve_api_key(provider: dict) -> str | None:
    if provider.get("api_key"):
        return provider["api_key"]
    env = provider.get("api_key_env")
    if env and os.environ.get(env):
        return os.environ[env]
    return None


def add_provider(
    name: str,
    type: str = "openai",
    base_url: str = "",
    label: str | None = None,
    api_key_env: str | None = None,
    api_key: str | None = None,
    models: list[str] | None = None,
    enabled: bool = True,
    cfg: dict | None = None,
) -> dict:
    cfg = cfg or load_config()
    llm = get_llm_cfg(cfg)
    providers = llm.setdefault("providers", {})
    providers[name] = {
        "type": type,
        "label": label or name,
        "base_url": base_url.rstrip("/"),
        "api_key_env": api_key_env or f"{name.upper()}_API_KEY",
        "api_key": api_key or "",
        "models": models or [],
        "enabled": enabled,
    }
    save_cfg(cfg)
    return providers[name]


def update_provider(name: str, **fields) -> dict:
    cfg = load_config()
    llm = get_llm_cfg(cfg)
    providers = llm.get("providers") or {}
    if name not in providers:
        raise KeyError(f"provider not found: {name}")
    p = providers[name]
    for k, v in fields.items():
        if v is None:
            continue
        if k == "models" and isinstance(v, str):
            v = [m.strip() for m in v.split(",") if m.strip()]
        if k == "enabled" and isinstance(v, str):
            v = v.lower() in ("1", "true", "yes", "on")
        p[k] = v
    if "base_url" in p and isinstance(p["base_url"], str):
        p["base_url"] = p["base_url"].rstrip("/")
    save_cfg(cfg)
    return p


def remove_provider(name: str) -> None:
    cfg = load_config()
    llm = get_llm_cfg(cfg)
    providers = llm.get("providers") or {}
    if name not in providers:
        raise KeyError(f"provider not found: {name}")
    del providers[name]
    if llm.get("active_provider") == name:
        remaining = list(providers.keys())
        llm["active_provider"] = remaining[0] if remaining else None
        llm["active_model"] = (providers.get(llm["active_provider"] or {}, {}).get("models") or [None])[0]
    save_cfg(cfg)


def use_provider(provider: str, model: str | None = None, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    llm = get_llm_cfg(cfg)
    providers = llm.get("providers") or {}
    if provider not in providers:
        raise KeyError(f"provider not found: {provider}")
    llm["active_provider"] = provider
    models = providers[provider].get("models") or []
    if model:
        llm["active_model"] = model
        if model not in models:
            providers[provider]["models"] = models + [model]
    else:
        llm["active_model"] = models[0] if models else None
    save_cfg(cfg)
    return {"provider": provider, "model": llm.get("active_model")}


def set_api_key(name: str, api_key: str, use_env: bool = False) -> dict:
    cfg = load_config()
    llm = get_llm_cfg(cfg)
    p = (llm.get("providers") or {}).get(name)
    if not p:
        raise KeyError(f"provider not found: {name}")
    if use_env:
        env = p.get("api_key_env") or f"{name.upper()}_API_KEY"
        os.environ[env] = api_key
        p["api_key"] = ""
    else:
        p["api_key"] = api_key
    save_cfg(cfg)
    return p


def active_provider(cfg: dict | None = None) -> tuple[dict, str | None]:
    llm = get_llm_cfg(cfg)
    name = llm.get("active_provider")
    providers = llm.get("providers") or {}
    if not name or name not in providers:
        raise RuntimeError("no active LLM provider — run: python -m paper_research models use ...")
    return providers[name], llm.get("active_model")


# --- optional chat call (OpenAI-compatible + Anthropic) ---

def chat(messages: list[dict], provider_name: str | None = None,
         model: str | None = None, temperature: float | None = None) -> str:
    cfg = load_config()
    llm = get_llm_cfg(cfg)
    if provider_name:
        provider = (llm.get("providers") or {})[provider_name]
    else:
        provider, _ = active_provider(cfg)
    model = model or llm.get("active_model")
    if not model:
        models = provider.get("models") or []
        model = models[0] if models else None
    if not model:
        raise RuntimeError("no model selected")
    key = resolve_api_key(provider)
    if not key:
        raise RuntimeError(
            f"missing API key for '{provider.get('label','')}': "
            f"set env {provider.get('api_key_env')} or models set-key"
        )
    temp = temperature if temperature is not None else float(llm.get("temperature", 0.2))
    timeout = int(llm.get("timeout_sec", 60))
    ptype = (provider.get("type") or "openai").lower()
    base = (provider.get("base_url") or "").rstrip("/")

    if ptype == "anthropic":
        url = f"{base}/messages"
        body = {
            "model": model,
            "max_tokens": 2048,
            "temperature": temp,
            "messages": messages,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
    else:
        url = f"{base}/chat/completions"
        body = {
            "model": model,
            "temperature": temp,
            "messages": messages,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            method="POST",
        )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if ptype == "anthropic":
        parts = data.get("content") or []
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def test_provider(name: str, model: str | None = None) -> dict:
    try:
        reply = chat(
            [{"role": "user", "content": "Reply with exactly: OK"}],
            provider_name=name,
            model=model,
            temperature=0,
        )
        return {"ok": True, "reply": reply[:80]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
