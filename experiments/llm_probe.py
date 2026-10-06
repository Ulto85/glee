"""Diagnose the neg-LLM HTTP 400. Reads .anthropic_key locally, makes one tiny haiku call, prints the
API's exact error body so we can see WHY (model id? version? account?). Run:  python3 experiments/llm_probe.py
Tries a few model-id / anthropic-version combos and reports which (if any) works."""
import json, urllib.request, urllib.error, os

KEY = open(os.path.join(os.path.dirname(__file__), "..", ".anthropic_key")).read().strip()
MODELS = ["claude-haiku-4-5-20251001", "claude-haiku-4-5", "claude-3-5-haiku-latest", "claude-3-5-haiku-20241022"]
VERSIONS = ["2023-06-01"]

def call(model, ver):
    body = json.dumps({"model": model, "max_tokens": 16,
                       "messages": [{"role": "user", "content": "reply OK"}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": KEY, "anthropic-version": ver, "content-type": "application/json"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())
        txt = next((b.get("text") for b in d.get("content", []) if b.get("type") == "text"), "?")
        return f"OK -> {txt!r}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode()[:300]}"
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:200]}"

print(f"key length {len(KEY)}, prefix {KEY[:7]}")
for m in MODELS:
    for v in VERSIONS:
        print(f"  model={m} ver={v}: {call(m, v)}")
