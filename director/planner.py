"""Text + current scene manifest -> Nemotron -> one validated scene plan.

Model output is untrusted text: we extract JSON, validate it against the shared
schema AND the real manifest, and allow at most ONE corrective retry. Nothing
from the model is ever eval'd or executed.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field

from .nemotron_client import ModelClient, ModelReply
from .schema import allowed_assets, manifest_objects, plan_schema, validate_plan
from .trace import Trace

log = logging.getLogger("director.planner")


class PlanError(RuntimeError):
    def __init__(self, message: str, errors: list[str], trace: Trace, raw: str = ""):
        super().__init__(message)
        self.errors, self.trace, self.raw = errors, trace, raw


@dataclass
class PlanResult:
    plan: dict
    trace: Trace
    model: str
    live: bool
    attempts: int
    replies: list[ModelReply] = field(default_factory=list)


def new_request_id() -> str:
    # time-sortable + random suffix => no collisions across runs or machines
    return time.strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3)


SYSTEM_PROMPT = """You are the planning brain of TinyDirector, an agent that edits a 3D Blender scene.
You never write code. You output exactly ONE JSON object that matches the JSON Schema below, and nothing else
(no prose, no markdown fences).

Operations:
- add: create a new object. id MUST be "<asset>_<n>" using the NEXT FREE ids listed below. Never reuse an existing id.
- modify: change color/position/rotation/scale of an EXISTING object. Put only the changed fields in "set".
- delete: remove an EXISTING object. Only when the user explicitly asks to remove something.
- animate: keyframe position/rotation/scale of an existing or just-added object from start_frame to end_frame (end > start).

Rules:
1. Supported assets ONLY: {assets}. Never invent assets or operations.
2. If the request needs something unsupported or is too ambiguous, return mode "clarify" with "operations": [] and a short
   "message" that says what is unsupported and offers the closest supported option. Do not silently substitute.
3. Edits: reference ids that exist in the scene manifest exactly. Touch ONLY what the user asked about; every other
   object must be left alone (do not delete and re-create; use modify). "the mug" means the existing mug.
4. Colors are "#RRGGBB". Units are meters, Z is up, ground at z=0, keep positions within [-10, 10].
   Place new objects so they do not overlap existing ones; "next to" means about 1.5 m apart.
5. Set "request_id" to "pending" (the system fills it in). "summary" is one short sentence.

JSON Schema:
{schema}
"""


def _next_free_ids(objects: list[dict], assets: list[str]) -> dict[str, str]:
    taken = {o.get("id") for o in objects}
    out = {}
    for a in assets:
        n = 1
        while f"{a}_{n}" in taken:
            n += 1
        out[a] = f"{a}_{n}"
    return out


def build_messages(text: str, manifest: dict | None, mode: str) -> list[dict]:
    assets = allowed_assets()
    objects = manifest_objects(manifest)
    compact_objects = [{k: o[k] for k in ("id", "asset", "color", "position", "rotation", "scale") if k in o}
                       for o in objects]
    system = SYSTEM_PROMPT.format(assets=", ".join(assets), schema=json.dumps(plan_schema(), separators=(",", ":")))
    mode_line = {"create": "create (add new objects; existing objects stay as they are)",
                 "edit": "edit (change the existing scene; reference manifest ids)",
                 "auto": "decide: create if adding new things, edit if changing existing things"}[mode]
    user = (
        f"<requested_mode>{mode if mode != 'auto' else 'auto'}</requested_mode>\n"
        f"Mode guidance: {mode_line}\n"
        f"<scene_manifest>\n{json.dumps(compact_objects)}\n</scene_manifest>\n"
        f"Next free ids per asset: {json.dumps(_next_free_ids(objects, assets))}\n"
        f"<user_request>{text}</user_request>\n"
        "Return the JSON plan now."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def extract_json(text: str) -> dict:
    """Pull the first complete top-level JSON object out of model text. Raises ValueError."""
    if not text or not text.strip():
        raise ValueError("model returned empty content (if thinking is on, raise max_tokens or set NEMOTRON_THINKING=0)")
    cleaned = _THINK_RE.sub("", text)
    if "<think>" in cleaned.lower():  # unterminated reasoning block
        cleaned = cleaned[cleaned.lower().rfind("</think>") + 8:] if "</think>" in cleaned.lower() else cleaned
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1)
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("no JSON object found in model output")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(cleaned)):
        c = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                obj = json.loads(cleaned[start:i + 1])
                if not isinstance(obj, dict):
                    raise ValueError("top-level JSON is not an object")
                return obj
    raise ValueError("JSON object is incomplete (output may have been cut off by max_tokens)")


def plan(text: str, client: ModelClient, manifest: dict | None = None, mode: str = "auto",
         request_id: str | None = None, trace: Trace | None = None) -> PlanResult:
    if mode not in ("create", "edit", "auto"):
        raise ValueError("mode must be create, edit or auto")
    text = text.strip()
    if not text:
        raise ValueError("empty request text")
    rid = request_id or new_request_id()
    trace = trace or Trace(rid, text)
    objects = manifest_objects(manifest)
    trace.add("intent", f"user asked ({mode}): {text!r}")
    ids = ", ".join(o["id"] for o in objects) or "(empty scene)"
    trace.add("read_world", f"{len(objects)} objects in scene: {ids}",
              scene_version=(manifest or {}).get("scene_version"))

    messages = build_messages(text, manifest, mode)
    expected = mode if mode in ("create", "edit") else None
    replies: list[ModelReply] = []
    errors: list[str] = []
    raw = ""
    for attempt in (1, 2):
        reply = client.complete(messages)
        replies.append(reply)
        raw = reply.content
        source = "LIVE" if reply.live else "MOCK"
        tokens = reply.usage.get("completion_tokens")
        trace.add("plan", f"{source} {reply.model} attempt {attempt} "
                          f"({reply.latency_s:.1f}s{f', {tokens} tokens' if tokens else ''}"
                          f"{', reasoning on' if reply.reasoning else ''})")
        log.debug("request=%s attempt=%d model=%s raw=%s", rid, attempt, reply.model, raw[:2000])
        try:
            obj = extract_json(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            errors = [f"could not parse JSON: {exc}"]
        else:
            obj["request_id"] = rid  # we own request ids, not the model
            errors = validate_plan(obj, manifest, expected_mode=expected)
            if not errors:
                n = len(obj["operations"])
                trace.add("validate", f"accepted {n} operation{'s' if n != 1 else ''}, rejected 0"
                          + (f" (after 1 repair)" if attempt == 2 else ""),
                          operations=[f"{o['op']} {o['id']}" for o in obj["operations"]])
                log.info("request=%s model=%s mode=%s ops=%d", rid, reply.model, obj["mode"], n)
                return PlanResult(obj, trace, reply.model, reply.live, attempt, replies)
        trace.add("validate", f"rejected attempt {attempt}: {len(errors)} error(s)", errors=errors)
        log.warning("request=%s attempt=%d invalid: %s", rid, attempt, errors)
        if attempt == 1:
            messages = messages + [
                {"role": "assistant", "content": raw[:6000]},
                {"role": "user", "content": "That plan was rejected by the validator:\n- " + "\n- ".join(errors)
                 + "\nReturn ONE corrected JSON object only. Use only ids from the scene manifest or the next free ids."},
            ]
    raise PlanError("model output failed validation after 1 retry; nothing was executed", errors, trace, raw)
