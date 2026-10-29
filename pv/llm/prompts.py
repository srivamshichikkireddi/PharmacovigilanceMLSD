"""L2: prompts are versioned artifacts. A prompt's version is its file stem; its content hash is
recorded too, so an edit that forgets to bump the version is still detectable."""
import hashlib
from pathlib import Path

DIR = Path(__file__).resolve().parents[2] / "prompts"


def load(name):
    p = DIR / f"{name}.txt"
    text = p.read_text(encoding="utf-8")
    return text, f"{name}@{hashlib.sha1(text.encode()).hexdigest()[:8]}"


def render(name, **vars):
    text, version = load(name)
    for k, v in vars.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text, version
