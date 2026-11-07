"""Check the plan against the scene Blender ACTUALLY reports afterwards.

Success is never taken from the model's word: every operation gets a concrete check against the
read-back state, and every object the plan did not mention must be unchanged (no duplicates, no
deleted bystanders, no reset animations).
"""

from __future__ import annotations

from dataclasses import dataclass

from .schema import BOUNCE_DEFAULTS

POS_TOL = 1e-3
COLOR_TOL = 2  # per 8-bit channel (sRGB <-> linear round trip)


@dataclass
class Check:
    ok: bool
    what: str
    detail: str = ""

    def line(self) -> str:
        return f"  {'ok  ' if self.ok else 'FAIL'} {self.what}" + (f" - {self.detail}" if self.detail else "")


def _objs(m):
    return {o["id"]: o for o in (m or {}).get("scene_objects", [])}


def _color_eq(a, b) -> bool:
    try:
        return all(abs(int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) <= COLOR_TOL for i in (1, 3, 5))
    except (TypeError, ValueError, IndexError):
        return False


def _vec_eq(a, b, tol=POS_TOL, angles=False) -> bool:
    if not (isinstance(a, list) and isinstance(b, list) and len(a) == len(b) == 3):
        return False
    if angles:
        return all(abs(((x - y) + 180) % 360 - 180) <= 0.01 for x, y in zip(a, b))
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _keys(obj, prop, axis):
    for ch in obj.get("animation", []) or []:
        if ch["property"] == prop and ch["axis"] == axis:
            return ch["keys"]
    return []


def _key_at(keys, frame):
    return next((v for f, v in keys if abs(f - frame) < 1e-6), None)


def _animated(obj, prop) -> bool:
    return any(ch["property"] == prop for ch in obj.get("animation", []) or [])


def check_bounce(obj: dict, op: dict) -> tuple[bool, str]:
    b = {**BOUNCE_DEFAULTS, **op}
    start, n, period, h = b["start_frame"], b["cycles"], b["frames_per_cycle"], float(b["height"])
    end = start + n * period
    keys = [k for k in _keys(obj, "position", "z") if start <= k[0] <= end]
    if not keys:
        return False, "no Z keyframes in the bounce range"
    base = keys[0][1]
    ground = [f for f, v in keys if abs(v - base) < POS_TOL]
    peaks = [f for f, v in keys if abs(v - (base + h)) < POS_TOL]
    ok = (len(peaks) == n and len(ground) == n + 1 and keys[0][0] == start and keys[-1][0] == end
          and abs(keys[-1][1] - base) < POS_TOL)
    return ok, f"{len(peaks)} peaks of {h:g} m, ground at frames {[int(f) if f == int(f) else f for f in ground]}"


def run(plan: dict, before: dict | None, after: dict) -> list[Check]:
    b, a = _objs(before), _objs(after)
    out: list[Check] = []
    touched: set[str] = set()
    for i, op in enumerate(plan.get("operations", [])):
        kind, oid = op["op"], op["id"]
        touched.add(oid)
        obj = a.get(oid)
        tag = f"op {i} {kind} {oid}"
        if kind == "delete":
            out.append(Check(oid not in a, tag, "gone" if oid not in a else "still in scene"))
            continue
        if obj is None:
            out.append(Check(False, tag, "object missing from Blender's read-back"))
            continue
        if kind == "add":
            problems = []
            if obj.get("asset") != op["asset"]:
                problems.append(f"asset {obj.get('asset')}")
            if op.get("color") and not _color_eq(obj.get("color"), op["color"]):
                problems.append(f"color {obj.get('color')} != {op['color']}")
            if not _animated(obj, "position") and not _vec_eq(obj.get("position"), op["position"]):
                problems.append(f"position {obj.get('position')}")
            out.append(Check(not problems, tag, "; ".join(problems) or f"{obj['asset']} at {obj['position']}"))
        elif kind == "modify":
            problems = []
            for key, val in op["set"].items():
                got = obj.get(key)
                if key == "color":
                    good = _color_eq(got, val)
                elif key == "scale":
                    good = got is not None and abs(got - val) <= POS_TOL
                else:
                    good = _vec_eq(got, val, angles=key == "rotation")
                if not good:
                    problems.append(f"{key} is {got}, wanted {val}")
            out.append(Check(not problems, tag, "; ".join(problems) or ", ".join(f"{k}={v}" for k, v in op["set"].items())))
        elif kind == "animate":
            problems = []
            for ax, target in zip("xyz", op["to"]):
                keys = _keys(obj, op["property"], ax)
                if _key_at(keys, op["start_frame"]) is None:
                    problems.append(f"{ax}: no key at {op['start_frame']}")
                end_v = _key_at(keys, op["end_frame"])
                if end_v is None or abs(end_v - target) > 0.01:
                    problems.append(f"{ax}: key at {op['end_frame']} is {end_v}, wanted {target}")
            out.append(Check(not problems, tag, "; ".join(problems)
                             or f"{op['property']} keyed {op['start_frame']}-{op['end_frame']}"))
        elif kind == "bounce":
            ok, detail = check_bounce(obj, op)
            out.append(Check(ok, tag, detail))

    # bystanders: anything the plan did not mention must be exactly as before
    if before is not None:
        changed = [i for i in b if i not in touched and i in a and _strip(b[i]) != _strip(a[i])]
        vanished = [i for i in b if i not in touched and i not in a]
        out.append(Check(not changed and not vanished, "untouched objects unchanged",
                         ("changed: " + ", ".join(changed) if changed else "")
                         + (" vanished: " + ", ".join(vanished) if vanished else "")
                         or f"{len([i for i in b if i not in touched])} object(s) verified"))
        adds = {op["id"] for op in plan.get("operations", []) if op["op"] == "add"}
        extra = [i for i in a if i not in b and i not in adds]
        out.append(Check(not extra, "no unexpected new objects", ", ".join(extra)))
    return out


def _strip(o: dict) -> dict:
    return {k: v for k, v in o.items() if k not in ("name", "material")}


def summary(checks: list[Check]) -> str:
    good = sum(c.ok for c in checks)
    return f"{good}/{len(checks)} checks passed"
