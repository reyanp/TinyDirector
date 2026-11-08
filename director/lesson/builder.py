"""Builder agent: a code-writing LLM (OpenAI by default) turns a RESOLVED lesson spec into toolkit code.

The model never touches Blender directly. It writes a short Python program that may only call the lesson
toolkit (blender_engine/toolkit.py API); the director checks it against the sandbox allowlist, and a separate
headless Blender runs it (lesson_runner.py). The verifier then reads the built scene back; on failure the
builder gets ONE repair turn with the concrete errors and measured geometry.

Config (env or the gitignored .env; the key is never printed):
  OPENAI_API_KEY          required for this builder (otherwise the deterministic builder is used, labeled)
  OPENAI_BUILDER_MODEL    default gpt-5-mini
  OPENAI_BASE_URL         optional (any OpenAI-compatible endpoint)
  OPENAI_TIMEOUT          seconds, default 180
  OPENAI_REASONING_EFFORT optional (low/medium/high), sent only when set
"""

from __future__ import annotations

import json
import os
import re
import time

from blender_engine import sandbox
from blender_engine.lesson_parts import COLORS, LED_COLORS, PARTS

from ..nemotron_client import ConfigError, ModelCallError, ModelReply

DEFAULT_MODEL = "gpt-5-mini"
TOOLKIT_NAMES = ("scene", "part", "pin", "wire", "flow", "label", "label_at", "key_points", "step", "box",
                 "cylinder", "sphere", "text3d")  # must equal toolkit.API (checked by a test)


class OpenAIBuilderClient:
    live = True

    def __init__(self, model: str | None = None, timeout: float | None = None):
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise ConfigError("OPENAI_API_KEY is not set (add it to the repo-root .env to use the builder agent)")
        from openai import OpenAI

        self.model = model or os.environ.get("OPENAI_BUILDER_MODEL", "").strip() or DEFAULT_MODEL
        self.effort = os.environ.get("OPENAI_REASONING_EFFORT", "").strip()
        base = os.environ.get("OPENAI_BASE_URL", "").strip() or None
        self.timeout = timeout or float(os.environ.get("OPENAI_TIMEOUT", "180"))
        self._client = OpenAI(api_key=key, base_url=base, timeout=self.timeout, max_retries=1)

    def complete(self, messages: list[dict], *, max_tokens: int | None = None) -> ModelReply:
        kwargs = {"model": self.model, "messages": messages, "max_completion_tokens": max_tokens or 16000}
        if self.effort:
            kwargs["reasoning_effort"] = self.effort
        start = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # normalize; never include the key
            status, code = getattr(exc, "status_code", None), getattr(exc, "code", None)
            hint = {401: "key rejected - check OPENAI_API_KEY", 404: f"model {self.model!r} not found",
                    429: "rate limited (too many requests/tokens per minute for this account tier)"}.get(status, "")
            if code == "insufficient_quota":
                hint = ("no API credits on this OpenAI account (insufficient_quota): add a payment method / "
                        "prepaid credits at platform.openai.com -> Settings -> Billing")
            raise ModelCallError(f"{type(exc).__name__}{f' {status}' if status else ''}: {hint or exc}") from None
        choice = resp.choices[0]
        usage = resp.usage.model_dump() if getattr(resp, "usage", None) else {}
        usage["finish_reason"] = choice.finish_reason
        return ModelReply(content=choice.message.content or "", model=resp.model or self.model,
                          latency_s=time.perf_counter() - start, usage=usage, live=True)


def _catalog() -> str:
    rows = []
    for name, p in PARTS.items():
        size = "size given by your primitives" if p["size"] is None else "footprint {} x {} m, height {} m".format(*p["size"])
        pins = "pins declared in the spec" if p["pins"] is None else "pins " + ", ".join(p["pins"])
        rows.append(f"- {name}: {p['desc']}. {pins}; {size}")
    return "\n".join(rows)


SYSTEM_PROMPT = """You are the BUILDER agent of a 3D electronics tutor. A director agent wrote a lesson spec
(what the scene must show, numbers already computed). You write a short Python program that builds the scene
in Blender through the toolkit below. Your job is a CLEAR, ACCURATE, READABLE 3D layout.

The program runs in a strict sandbox: NO imports, NO attribute access (no `a.b` at all), no while/try/with/
lambda/class. Calls must be plain names: the toolkit functions, these builtins (range len min max abs round
enumerate zip list dict tuple float int str sorted sum any all reversed print bool), math names (pi sin cos tan
atan2 sqrt radians degrees hypot floor ceil), or functions you define. Names must not start with "_".

TOOLKIT (coordinates in meters; board top is z=0; X to the right, Y away from the camera; the camera looks
from the front (-Y) and above, slightly from the right):
  scene(title)                                   # first call; title = spec title, verbatim
  part(id, type, x=0, y=0, rotate=0, label=None, color=None, pins=None)
      # places a catalog part; its label is shown automatically above it as label id "<id>_label".
      # rotate = degrees about Z. color: LED color for led ("red","green","blue","yellow","white"), body color
      # otherwise. type "custom" needs pins={"name": (dx, dy, dz), ...} relative to the part, then draw it
      # with box/cylinder/sphere/text3d(..., part=id).
  pin("q1.B") -> (x, y, z)                       # world position of a pin, after the part is placed
  wire(id, "a.pin", "b.pin", via=None, color=None, height=0.14)
      # a wire from pin to pin. Default route: up from the first pin, one right-angle turn (along X then Y,
      # or Y then X when the Y distance is longer), down to the second pin. Pass via=[(x, y), ...]
      # waypoints (z defaults to `height`) to route around parts; the route is smoothed.
  flow(id, [wire ids in current direction], current_ma=<number>, label=None, color="yellow")
      # glowing conventional-current markers; speed is proportional to current_ma (computed for you);
      # its label is shown as "<id>_label" near the middle of the path.
  label(id, text, near=None, x=None, y=None, z=None, size=0.16, color="white")
      # a camera-facing text plate near a part/wire id, or at x, y, z (absolute).
  label_at(id, x=None, y=None, z=None)           # move a label: a spec label by ITS id ("l1"), or the automatic
                                                 # "<part id>_label" / "<flow id>_label" (only parts and flows
                                                 # get automatic labels; there is no "l1_label")
  key_points([str, ...])                         # 1-4 bullet points shown in the top-right panel
  step([ids], caption)                           # reveal order: one call per spec step, in order
  box(id, sx, sy, sz, x, y, z, part=None, color="gray", emit=0.0)        # primitives for custom parts or
  cylinder(id, r, depth, x, y, z, axis="z", part=None, color="gray", emit=0.0)   # small props; with part=
  sphere(id, r, x, y, z, part=None, color="gray", emit=0.0)              # coordinates are relative to it
  text3d(id, text, x, y, z, size=0.15, part=None, color="white", flat=True)
Colors: {colors}. LED colors: {led_colors}.
The runner adds the board, camera framing, lights, title/caption/key-point HUD and the step animation after
your code. Do not try to do those.

PART CATALOG (pins are the only connection points):
{catalog}

CONTENT RULES (a verifier checks every one against what Blender actually built):
- Use EXACTLY the spec ids: one part() per spec part (same id, type, label text, color), one wire() per spec
  connection (same id, same two pins), one flow() per spec flow (same id, same wire path, the given
  current_ma number, the given label), one label() per spec label (same id, text, near). No extra wires.
- key_points(spec key_points) and one step(show, caption) per spec step, texts verbatim.
- Never write a number on screen that is not in the spec (no extra labels with values).

LAYOUT RULES (this is where you add value):
- Arrange each circuit as a clean loop in the order current flows. Related parts side by side; separate
  sub-circuits (spec "group") into their own regions; shared parts (supply, ground, signal) where every group
  can reach them with short wires.
- Leave at least 0.8 m between part footprints. Keep everything within about x in [-7, 7], y in [-3.5, 4].
- No wire may pass under/over another part's footprint. A part placed at (x, y) with footprint W x D occupies
  x - W/2 .. x + W/2 and y - D/2 .. y + D/2 (a resistor at x=0 spans x -1.0..1.0). Every straight segment
  pin -> via -> ... -> pin must stay at least 0.3 m outside every OTHER part's footprint, so put waypoints in
  the free lanes between rows/columns or outside the whole layout. Check this with arithmetic before you write
  the call. Wires to the same pin from different directions are fine; avoid long wires crossing each other.
- Put tall parts (battery, LEDs, MOSFET) further back (+Y) than short ones when they would hide them.
- Labels must not overlap each other or cover other parts. Every part label already sits above its part;
  only move crowded ones with label_at(...). A label must never be inside a part: above a part, z must be at
  least the part height + 0.3 (resistor 1.0, battery 2.1, LED 2.2); otherwise move it beside the part, on the
  free board (z about 0.3). Put spec labels where they explain something.
- Overlap is judged ON SCREEN. The camera looks from the front (-Y) and above, so a label raised high near the
  front lands on top of labels and parts further back. Keep labels low (about 0.3-1.0 above their part) and
  separate neighbours sideways in X rather than stacking them in Z.
- rotate parts (multiples of 90) when it makes the wiring shorter or cleaner.

Reply with ONE ```python code block and nothing else."""


def system_prompt() -> str:
    return (SYSTEM_PROMPT.replace("{colors}", ", ".join(COLORS)).replace("{led_colors}", ", ".join(LED_COLORS))
            .replace("{catalog}", _catalog()))


def first_messages(resolved_spec: dict, draft_code: str) -> list[dict]:
    view = {k: resolved_spec[k] for k in ("title", "question", "parts", "connections", "flows", "labels",
                                          "key_points", "steps")}
    user = ("LESSON SPEC (numbers already resolved):\n" + json.dumps(view, indent=1, ensure_ascii=False) +
            "\n\nA deterministic draft program follows. Its CONTENT is correct (ids, texts, currents) but its "
            "layout is naive: parts on a plain grid, wires crossing parts, crowded labels. Write the improved "
            "program.\n```python\n" + draft_code + "```")
    return [{"role": "system", "content": system_prompt()}, {"role": "user", "content": user}]


def geometry_note(readback: dict) -> str:
    """Measured facts from the built scene, so the repair can move things precisely."""
    if not readback.get("ok"):
        return ""
    rows = []
    for pid, p in sorted(readback.get("parts", {}).items()):
        (x0, y0, _), (x1, y1, z1) = p["bbox"]
        rows.append(f"  {pid}: footprint x {x0:.2f}..{x1:.2f}, y {y0:.2f}..{y1:.2f}, top z {z1:.2f}")
    return "Measured part footprints in the built scene:\n" + "\n".join(rows)


def repair_messages(previous: list[dict], code: str, errors: list[str], readback: dict) -> list[dict]:
    note = geometry_note(readback)
    msg = ("The verifier rejected the scene built from your program:\n- " + "\n- ".join(errors[:25]) +
           (f"\n\n{note}" if note else "") +
           "\n\nFix every issue and reply with the complete corrected program in ONE ```python block.")
    return previous + [{"role": "assistant", "content": f"```python\n{code}```"}, {"role": "user", "content": msg}]


_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)


def extract_code(text: str) -> str:
    blocks = _FENCE.findall(text or "")
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    stripped = (text or "").strip()
    if stripped.startswith("scene("):
        return stripped + "\n"
    raise ValueError("no ```python code block in the builder reply")


def ask(client, messages: list[dict]) -> tuple[str, list[str], ModelReply]:
    """One builder call -> (code, sandbox errors, reply). Sandbox errors are checked director-side first."""
    reply = client.complete(messages)
    try:
        code = extract_code(reply.content)
    except ValueError as exc:
        return "", [str(exc)], reply
    return code, sandbox.check(code, set(TOOLKIT_NAMES)), reply
