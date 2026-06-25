import os
import json
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "gemma3:latest"

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# OpenAI model pricing (USD per 1M tokens)
PRICING = {
    "gpt-4o":      {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}

# Persistent cost tracking
COST_FILE = Path("openai_cost.json")


def _load_cost() -> dict:
    if COST_FILE.exists():
        try:
            with open(COST_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"total_usd": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0}


def _save_cost(data: dict):
    try:
        with open(COST_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[cost] failed to save: {e}")

def _to_openai_messages(contents: list, system_prompt: str = None) -> list:
    """Convert our internal {role, parts:[{text}]} format to OpenAI's."""
    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    for item in contents:
        role = item["role"]
        if role == "model":
            role = "assistant"
        text = item["parts"][0]["text"]
        msgs.append({"role": role, "content": text})
    return msgs


def _to_ollama_messages(contents: list, system_prompt: str = None) -> list:
    """Convert our internal format to Ollama's."""
    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    for item in contents:
        role = item["role"]
        if role == "model":
            role = "assistant"
        msgs.append({"role": role, "content": item["parts"][0]["text"]})
    return msgs

def call_ollama(contents: list, system_prompt: str = None) -> str:
    messages = _to_ollama_messages(contents, system_prompt)
    print(f"[ollama] calling {OLLAMA_MODEL}...")

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": True,
            "options": {"temperature": 0.2, "num_predict": 4096, "num_ctx": 8192},
        },
        stream=True,
        timeout=600,
    )

    full = ""
    for line in response.iter_lines():
        if not line:
            continue
        chunk = json.loads(line)
        if "error" in chunk:
            raise Exception(f"Ollama error: {chunk['error']}")
        if chunk.get("message", {}).get("content"):
            full += chunk["message"]["content"]
        if chunk.get("done"):
            break

    if not full:
        raise Exception("No response from Ollama")
    print("[ollama] done.")
    return full

def call_openai(contents: list, system_prompt: str = None, model: str = "gpt-4o-mini") -> str:
    if not OPENAI_API_KEY:
        raise Exception("OPENAI_API_KEY not set in environment/.env file")

    messages = _to_openai_messages(contents, system_prompt)
    print(f"[openai] calling {model}...")

    response = requests.post(
        OPENAI_URL,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 4096,
        },
        timeout=120,
    )

    if response.status_code != 200:
        raise Exception(f"OpenAI error {response.status_code}: {response.text}")

    data = response.json()
    text = data["choices"][0]["message"]["content"]

    # cost tracking
    usage = data.get("usage", {})
    in_tok = usage.get("prompt_tokens", 0)
    out_tok = usage.get("completion_tokens", 0)
    pricing = PRICING.get(model, {"input": 0, "output": 0})
    call_cost = (in_tok / 1_000_000) * pricing["input"] + (out_tok / 1_000_000) * pricing["output"]

    cost_data = _load_cost()
    cost_data["total_usd"] += call_cost
    cost_data["calls"] += 1
    cost_data["tokens_in"] += in_tok
    cost_data["tokens_out"] += out_tok
    _save_cost(cost_data)

    print(f"[openai] {model} | in={in_tok} out={out_tok} | call=${call_cost:.4f} | total=${cost_data['total_usd']:.3f} ({cost_data['calls']} calls)")

    return text

def call_llm(contents: list, system_prompt: str = None, mode: str = "auto") -> str:
    """
    Route to the right backend based on mode.

    modes:
      'auto'      — default, picks based on content (editing vs new page)
      'new_page'  — brand new page generation, uses gpt-4o
      'edit'      — editing existing HTML, uses gpt-4o-mini
      'partial'   — section regen, uses gpt-4o-mini
      'cheap'     — improve_prompt / summarise_history, uses Ollama
    """

    if mode == "cheap":
        return call_ollama(contents, system_prompt)

    if mode == "new_page":
        return call_openai(contents, system_prompt, model="gpt-4o")

    if mode in ("edit", "partial"):
        return call_openai(contents, system_prompt, model="gpt-4o-mini")

    # auto-detect for backwards compat
    last_user = ""
    for item in reversed(contents):
        if item["role"] == "user":
            last_user = item["parts"][0]["text"]
            break

    # if the message contains the current HTML, it's an edit
    if "<!DOCTYPE" in last_user or "Current HTML:" in last_user:
        return call_openai(contents, system_prompt, model="gpt-4o-mini")

    # otherwise it's a new page / first generation
    return call_openai(contents, system_prompt, model="gpt-4o")


def get_cost_summary() -> dict:
    """Return current cost totals for display in an endpoint."""
    return _load_cost()


def reset_cost_tracking():
    """Zero out the cost tracker."""
    _save_cost({"total_usd": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0})