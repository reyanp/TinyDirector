"""Deterministic (non-LLM) builder: lesson spec -> toolkit code with a simple automatic layout.

Used (1) when no builder LLM is configured, (2) as the last resort when the builder agent's scene fails
verification twice, and (3) as the offline stand-in in tests. Its output passes the same sandbox and the
same structural checks; its layout is plain (parts on a grid per group), so layout issues are warnings.
"""

from __future__ import annotations

from collections import deque

from blender_engine.lesson_parts import PARTS

DX, DY = 3.0, 2.6


def _order(spec: dict, ids: list[str]) -> list[str]:
    """Parts in circuit order: breadth-first from a source (battery / signal) over connections."""
    adj = {i: [] for i in ids}
    for c in spec["connections"]:
        a, b = c["from"].split(".")[0], c["to"].split(".")[0]
        if a in adj and b in adj:
            adj[a].append(b)
            adj[b].append(a)
    types = {p["id"]: p["type"] for p in spec["parts"]}
    starts = [i for i in ids if types[i] in ("battery", "signal")] + ids
    seen, out = set(), []
    for s in starts:
        if s in seen:
            continue
        q = deque([s])
        seen.add(s)
        while q:
            n = q.popleft()
            out.append(n)
            for m in adj[n]:
                if m not in seen:
                    seen.add(m)
                    q.append(m)
    return out


def layout(spec: dict) -> dict[str, tuple[float, float]]:
    groups: dict[str, list[str]] = {}
    for p in spec["parts"]:
        groups.setdefault(p.get("group") or "", []).append(p["id"])
    shared = groups.pop("", [])
    pos: dict[str, tuple[float, float]] = {}
    if not groups:  # one circuit: a two-row loop in current order (top row left->right, bottom row back)
        ids = _order(spec, shared)
        top = (len(ids) + 1) // 2
        for k, pid in enumerate(ids):
            if k < top:
                pos[pid] = ((k - (top - 1) / 2) * DX, DY / 2)
            else:
                j = len(ids) - 1 - k
                pos[pid] = ((j - (top - 1) / 2) * DX + (DX / 2 if len(ids) % 2 else 0), -DY / 2)
        return pos
    for i, pid in enumerate(_order(spec, shared)):  # shared parts on a back row
        pos[pid] = ((i - (len(shared) - 1) / 2) * DX, DY * 1.2)
    names = sorted(groups)
    for g_i, g in enumerate(names):
        ids = _order(spec, groups[g])
        x0 = (g_i - (len(names) - 1) / 2) * DX * 2.4
        for k, pid in enumerate(ids):
            col, row = k % 2, k // 2
            pos[pid] = (x0 + (col - 0.5) * DX, -row * DY)
    return pos


def _boxes(spec: dict, pos: dict) -> dict[str, tuple[float, float, float, float]]:
    """Approximate footprints (x0, y0, x1, y1) with a small margin, from the catalog sizes."""
    out = {}
    for p in spec["parts"]:
        sx, sy = (PARTS[p["type"]]["size"] or (1.0, 0.6, 0.5))[:2]
        x, y = pos[p["id"]]
        out[p["id"]] = (x - sx / 2 - 0.15, y - sy / 2 - 0.15, x + sx / 2 + 0.15, y + sy / 2 + 0.15)
    return out


def _seg_hits(a, b, box) -> bool:
    """Axis-aligned segment a-b crosses box?"""
    x0, y0, x1, y1 = box
    if abs(a[1] - b[1]) < 1e-9:  # along X
        return y0 < a[1] < y1 and min(a[0], b[0]) < x1 and max(a[0], b[0]) > x0
    return x0 < a[0] < x1 and min(a[1], b[1]) < y1 and max(a[1], b[1]) > y0


def _clear(points, boxes, own) -> bool:
    return not any(_seg_hits(a, b, bx) for a, b in zip(points, points[1:]) for pid, bx in boxes.items()
                   if pid not in own)


# rough pin offsets for routing only (exact pins are measured in Blender; the verifier checks contact)
_PIN_DXY = {("resistor", "a"): (-1.0, 0.0), ("resistor", "b"): (1.0, 0.0), ("led", "anode"): (0.0, 0.15),
            ("led", "cathode"): (0.0, -0.15), ("npn_bjt", "E"): (-0.1, 0.0), ("npn_bjt", "C"): (0.1, 0.0),
            ("n_mosfet", "G"): (-0.25, 0.0), ("n_mosfet", "S"): (0.25, 0.0), ("battery", "+"): (0.0, 0.2),
            ("battery", "-"): (0.0, -0.2), ("switch", "a"): (-0.3, 0.0), ("switch", "b"): (0.3, 0.0)}


def _pin_xy(spec: dict, pos: dict, ref: str) -> tuple[float, float]:
    pid, _, pin = ref.partition(".")
    ptype = next(p["type"] for p in spec["parts"] if p["id"] == pid)
    dx, dy = _PIN_DXY.get((ptype, pin), (0.0, 0.0))
    return pos[pid][0] + dx, pos[pid][1] + dy


def _routes(spec: dict, pos: dict) -> dict[str, list[tuple[float, float]]]:
    """via waypoints for wires whose default right-angle route would pass over another part."""
    boxes = _boxes(spec, pos)
    ys = [v for bx in boxes.values() for v in (bx[1], bx[3])]
    front, back = min(ys) - 0.35, max(ys) + 0.35
    out, used = {}, 0
    for c in spec["connections"]:
        a, b = c["from"].split(".")[0], c["to"].split(".")[0]
        pa, pb = _pin_xy(spec, pos, c["from"]), _pin_xy(spec, pos, c["to"])
        own = {a, b}
        corner = (pa[0], pb[1]) if abs(pb[1] - pa[1]) > abs(pb[0] - pa[0]) else (pb[0], pa[1])
        if _clear([pa, corner, pb], boxes, own):
            continue
        for k in range(8):  # alternate front / back lanes, each wire on its own lane
            lane = front - 0.3 * (used + k // 2) if k % 2 == 0 else back + 0.3 * (used + k // 2)
            pts = [pa, (pa[0], lane), (pb[0], lane), pb]
            if _clear(pts, boxes, own):
                out[c["id"]] = [(pa[0], lane), (pb[0], lane)]
                used += 1
                break
    return out


def code_for(spec: dict) -> str:
    """Toolkit program for a RESOLVED spec (flows carry numeric current_ma)."""
    pos = layout(spec)
    lines = [f"scene({spec['title']!r})"]
    for p in spec["parts"]:
        x, y = pos[p["id"]]
        extra = ""
        if p["type"] == "custom":
            extra = ", pins={" + ", ".join(f"{n!r}: ({(k - (len(p['pins']) - 1) / 2) * 0.5:.2f}, -0.3, 0.05)"
                                          for k, n in enumerate(p["pins"])) + "}"
        if p.get("color") and p["type"] in ("led", "switch", "capacitor"):
            extra += f", color={p['color']!r}"
        lines.append(f"part({p['id']!r}, {p['type']!r}, x={x:.2f}, y={y:.2f}, label={p['label']!r}{extra})")
        if p["type"] == "custom":
            lines.append(f"box({p['id'] + '_body'!r}, 1.0, 0.6, 0.5, part={p['id']!r}, color='gray')")
    routes = _routes(spec, pos)
    for c in spec["connections"]:
        color = f", color={c['color']!r}" if c.get("color") else ""
        via = ""
        if c["id"] in routes:
            via = ", via=[" + ", ".join(f"({x:.2f}, {y:.2f})" for x, y in routes[c["id"]]) + "]"
        lines.append(f"wire({c['id']!r}, {c['from']!r}, {c['to']!r}{color}{via})")
    for f in spec["flows"]:
        lines.append(f"flow({f['id']!r}, {f['path']!r}, current_ma={f['current_ma']!r}, label={f['label']!r})")
    for lab in spec["labels"]:
        lines.append(f"label({lab['id']!r}, {lab['text']!r}, near={lab['near']!r})")
    lines.append(f"key_points({spec['key_points']!r})")
    for s in spec["steps"]:
        lines.append(f"step({s['show']!r}, {s['caption']!r})")
    return "\n".join(lines) + "\n"


assert all("pins" in v for v in PARTS.values())
