"""Flood & Damage Triage backend package.

Loading ``.env`` here means any entry point (uvicorn, the scripts in
``scripts/``, or a direct import) picks up local configuration before the
submodules read the environment. ``override=False`` keeps real environment
variables authoritative over the file.
"""
from __future__ import annotations

from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:  # python-dotenv is optional; the app still runs
    pass
