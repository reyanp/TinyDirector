"""Visual self-check: Nemotron VL looks at Blender's actual render and judges whether it
matches the user's request. If not, it proposes ONE short corrective instruction, which goes
back through the normal planner + validator (so the fix is still schema-checked data).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path

from .nemotron_client import ModelReply
from .planner import extract_json

VERIFY_PROMPT = """You are the quality checker for TinyDirector, an agent that builds 3D scenes in Blender.
The user asked: "{request}"
The plan that was executed: {ops}
Blender reports these objects now exist (ids, assets, hex colors, positions in meters, Z up):
{objects}

Look at the attached render of the scene. Judge ONLY whether the visible result satisfies the user's request
(right objects, colors, relative placement like "next to"/"on"/"behind", nothing important hidden or
overlapping). Ignore lighting quality and the simple low-poly art style. This is a still image of frame 1:
animations (bounce, spin, move over time) cannot be seen here, so never report them as missing.

Reply with ONE JSON object and nothing else:
{{"matches": true/false, "confidence": 0.0-1.0, "observations": "one sentence describing what you see",
  "issues": ["short issue", ...], "fix_request": "one short instruction to fix it, or empty string"}}
The fix_request must use only these edits on existing objects: change color, move, rotate, resize, delete,
or add a supported asset. Use object ids. If it matches, fix_request is ""."""


@dataclass
class Verdict:
    matches: bool
    confidence: float
    observations: str
    issues: list[str] = field(default_factory=list)
    fix_request: str = ""
    model: str = ""
    latency_s: float = 0.0
    live: bool = True


def _data_url(image: Path) -> str:
    mime = "image/jpeg" if image.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return f"data:{mime};base64,{base64.b64encode(image.read_bytes()).decode()}"


def build_messages(request: str, plan: dict, result: dict, image: Path) -> list[dict]:
    ops = [f"{o['op']} {o['id']}" + (f" {o.get('set')}" if o["op"] == "modify" else "")
           + (f" x{o.get('cycles')}" if o["op"] == "bounce" else "") for o in plan["operations"]]
    objects = [{k: o.get(k) for k in ("id", "asset", "color", "position", "scale")}
               for o in result.get("scene_objects", [])]
    text = VERIFY_PROMPT.format(request=request.replace('"', "'"), ops=json.dumps(ops), objects=json.dumps(objects))
    return [{"role": "user", "content": [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": _data_url(image)}},
    ]}]


def verify(request: str, plan: dict, result: dict, image: Path, client) -> Verdict:
    reply: ModelReply = client.complete(build_messages(request, plan, result, image), max_tokens=1024)
    try:
        data = extract_json(reply.content)
        verdict = Verdict(
            matches=bool(data.get("matches")),
            confidence=max(0.0, min(1.0, float(data.get("confidence", 0.0)))),
            observations=str(data.get("observations", ""))[:300],
            issues=[str(i)[:160] for i in (data.get("issues") or [])][:5],
            fix_request=str(data.get("fix_request") or "")[:300].strip(),
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        # an unreadable verdict is treated as "unverified", never as a reason to act
        verdict = Verdict(matches=True, confidence=0.0, observations="verifier reply was not valid JSON; skipped")
    verdict.model, verdict.latency_s, verdict.live = reply.model, reply.latency_s, reply.live
    return verdict


class MockVerifierModel:
    """Offline stand-in. Always labeled MOCK; returns a canned 'matches' verdict."""
    live = False
    model = "mock/verifier (NOT Nemotron)"

    def __init__(self, reply: dict | None = None):
        self.reply = reply or {"matches": True, "confidence": 0.5, "observations": "mock verifier: not inspected",
                               "issues": [], "fix_request": ""}
        self.calls = []

    def complete(self, messages, *, max_tokens=None) -> ModelReply:
        self.calls.append(messages)
        return ModelReply(content=json.dumps(self.reply), model=self.model, live=False)
