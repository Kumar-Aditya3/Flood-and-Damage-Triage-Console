"""Exercise backend.vision.describe() against the stub, checking all 3 tiers."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import llm, vision  # noqa: E402

EVENT = ROOT / "data" / "samples" / "sindh-town-floods-2022"
cands = json.loads((EVENT / "analysis" / "result.json").read_text(encoding="utf-8"))["candidates"]
cand = cands[1]

print("== endpoint normalization ==")
import os
for base, expect in [
    ("http://127.0.0.1:8899/v1", "http://127.0.0.1:8899/v1/chat/completions"),
    ("http://127.0.0.1:8899/v1/", "http://127.0.0.1:8899/v1/chat/completions"),
    ("http://127.0.0.1:8899/v1/chat/completions", "http://127.0.0.1:8899/v1/chat/completions"),
    ("https://host/openai/v1", "https://host/openai/v1/chat/completions"),
]:
    os.environ["LLM_BASE_URL"] = base
    got = llm.endpoint()
    print(f"  {'OK ' if got == expect else 'FAIL'} {base!r} -> {got!r}")

print("\n== tier 1: cloud configured and reachable ==")
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:8899/v1"
os.environ["LLM_API_KEY"] = "test-key-123"
os.environ["LLM_MODEL"] = "gpt-4o-mini"
print("  available:", llm.available())
print("  host_label:", llm.host_label())
out = vision.describe(EVENT, cand)
print("  source:", out["source"])
print("  text:", out["text"][:120])

print("\n== tier 2: cloud unreachable -> Ollama -> heuristic ==")
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:9999/v1"  # nothing listening
out2 = vision.describe(EVENT, cand)
print("  source:", out2["source"])
print("  text:", out2["text"][:120])

print("\n== tier 3: cloud unconfigured -> heuristic ==")
os.environ["LLM_API_KEY"] = ""
out3 = vision.describe(EVENT, cand)
print("  source:", out3["source"])
print("  text:", out3["text"][:120])

print("\n== auth header + no images sent ==")
import urllib.request
req = urllib.request.Request("http://127.0.0.1:8899/")
print(" ", urllib.request.urlopen(req).read().decode())
