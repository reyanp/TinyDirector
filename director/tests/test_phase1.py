"""Phase 1: scene awareness, bounce, multi-step plans, atomic execution, post-checks. Offline."""

import copy
import json
import threading
import time

import pytest

from director import bridge, postcheck
from director.dev import fake_worker
from director.engine_sim import apply_plan_full, make_result, value_at
from director.mock_model import RuleMockModel
from director.planner import build_messages, plan
from director.schema import validate_plan, validate_result

RED, BLUE = "#D95C52", "#3B6FD9"


def P(*ops, mode="edit"):
    return {"schema_version": 1, "request_id": "test_req_01", "mode": mode, "summary": "t", "operations": list(ops)}


def run_sim(manifest, p):
    m = manifest or {"scene_version": 0, "scene_objects": []}
    return make_result(p["request_id"], m["scene_version"], m["scene_objects"], p, m.get("scene"))


def by_id(m):
    return {o["id"]: o for o in m["scene_objects"]}


# ---------- contract ----------

def test_bounce_and_cabin_are_valid():
    p = P({"op": "add", "id": "cabin_1", "asset": "cabin", "position": [0, 0, 0]},
          {"op": "add", "id": "cube_1", "asset": "cube", "position": [3, 0, 0]},
          {"op": "bounce", "id": "cube_1", "cycles": 3, "height": 1.5}, mode="create")
    assert validate_plan(p) == []


@pytest.mark.parametrize("bad,needle", [
    ({"cycles": 0}, "less than the minimum"),
    ({"cycles": 3, "python": "x"}, "Additional properties"),
    ({"cycles": 3, "height": -1}, "less than or equal to the minimum"),
    ({"cycles": 20, "frames_per_cycle": 240}, "bounce ends at frame"),
])
def test_bad_bounce_rejected(bad, needle):
    p = P({"op": "add", "id": "cube_1", "asset": "cube", "position": [0, 0, 0]}, {"op": "bounce", "id": "cube_1", **bad})
    errs = validate_plan(p)
    assert any(needle in e for e in errs), errs


def test_bounce_on_unknown_object_rejected():
    errs = validate_plan(P({"op": "bounce", "id": "cube_1", "cycles": 3}))
    assert any("unknown object 'cube_1'" in e for e in errs)


def test_rich_result_matches_result_schema():
    r = run_sim(None, P({"op": "add", "id": "cube_1", "asset": "cube", "position": [0, 0, 0]},
                        {"op": "bounce", "id": "cube_1", "cycles": 2}))
    assert validate_result(r) == [] and r["op_results"][1] == {"index": 1, "op": "bounce", "id": "cube_1", "ok": True}


# ---------- simulator semantics (mirror of blender_engine/engine.py) ----------

def test_bounce_keys_start_and_end_on_ground():
    r = run_sim(None, P({"op": "add", "id": "cube_1", "asset": "cube", "position": [1, 2, 0]},
                        {"op": "bounce", "id": "cube_1", "cycles": 3, "height": 1.5}))
    keys = by_id(r)["cube_1"]["animation"][0]["keys"]
    assert keys == [[1, 0], [13, 1.5], [25, 0], [37, 1.5], [49, 0], [61, 1.5], [73, 0]]
    assert (r["scene"]["frame_start"], r["scene"]["frame_end"]) == (1, 73)


def test_chained_animate_keeps_the_peak():
    """The original bug: the second segment's start key overwrote the first segment's end key."""
    r = run_sim(None, P({"op": "add", "id": "cube_1", "asset": "cube", "position": [0, 0, 0]},
                        {"op": "animate", "id": "cube_1", "property": "position", "start_frame": 1, "end_frame": 12, "to": [0, 0, 2]},
                        {"op": "animate", "id": "cube_1", "property": "position", "start_frame": 12, "end_frame": 24, "to": [0, 0, 0]}))
    cube = by_id(r)["cube_1"]
    assert [value_at(cube, "position", 2, f) for f in (1, 12, 24)] == [0, 2, 0]


def test_recolor_and_move_keep_the_bounce():
    r1 = run_sim(None, P({"op": "add", "id": "cube_1", "asset": "cube", "position": [0, 0, 0]},
                         {"op": "bounce", "id": "cube_1", "cycles": 3}))
    r2 = run_sim(r1, P({"op": "modify", "id": "cube_1", "set": {"color": BLUE, "position": [2, 0, 0]}}))
    a1, a2 = by_id(r1)["cube_1"]["animation"], by_id(r2)["cube_1"]["animation"]
    assert a1 == a2 and by_id(r2)["cube_1"]["color"] == BLUE
    r3 = run_sim(r2, P({"op": "modify", "id": "cube_1", "set": {"position": [2, 0, 0.5]}}))
    assert [k[1] for k in by_id(r3)["cube_1"]["animation"][0]["keys"]][:2] == [0.5, 1.5]  # whole bounce lifted


def test_one_bad_op_means_nothing_is_applied():
    r1 = run_sim(None, P({"op": "add", "id": "cube_1", "asset": "cube", "position": [0, 0, 0], "color": RED}))
    objs, applied, errs, rows, _ = apply_plan_full(
        r1["scene_objects"], P({"op": "modify", "id": "cube_1", "set": {"color": BLUE}},
                               {"op": "bounce", "id": "ghost_1", "cycles": 2}))
    assert applied == 0 and objs == r1["scene_objects"] and [r["ok"] for r in rows] == [False, False]
    assert "unknown object ghost_1" in errs[0]


# ---------- post-check (success is measured, not assumed) ----------

def test_postcheck_passes_on_real_readback():
    p = P({"op": "add", "id": "tree_1", "asset": "tree", "position": [3, 0, 0]},
          {"op": "bounce", "id": "cube_1", "cycles": 3})
    before = run_sim(None, P({"op": "add", "id": "cube_1", "asset": "cube", "position": [0, 0, 0]},
                             {"op": "add", "id": "cabin_1", "asset": "cabin", "position": [-3, 0, 0]}))
    after = run_sim(before, p)
    checks = postcheck.run(p, before, after)
    assert all(c.ok for c in checks), [c.line() for c in checks]
    assert any("3 peaks" in c.detail for c in checks)


def test_postcheck_catches_bystander_change_and_bad_bounce():
    p = P({"op": "bounce", "id": "cube_1", "cycles": 3})
    before = run_sim(None, P({"op": "add", "id": "cube_1", "asset": "cube", "position": [0, 0, 0]},
                             {"op": "add", "id": "cabin_1", "asset": "cabin", "position": [-3, 0, 0]}))
    after = copy.deepcopy(run_sim(before, p))
    for o in after["scene_objects"]:
        if o["id"] == "cabin_1":
            o["color"] = "#000000"  # an engine bug touched the cabin
        if o["id"] == "cube_1":
            o["animation"][0]["keys"] = o["animation"][0]["keys"][:2]  # jumped once and froze
    failed = [c for c in postcheck.run(p, before, after) if not c.ok]
    assert {c.what for c in failed} == {"op 0 bounce cube_1", "untouched objects unchanged"}


# ---------- planner sees the live scene ----------

def test_prompt_has_sizes_bounce_and_animation_state():
    m = run_sim(None, P({"op": "add", "id": "cube_1", "asset": "cube", "position": [0, 0, 0]},
                        {"op": "bounce", "id": "cube_1", "cycles": 3}))
    m["other_objects"] = [{"name": "Suzanne", "type": "MESH"}]
    system, user = (x["content"] for x in build_messages("make the cube red", m, "auto"))
    assert "bounce" in system and "cabin: 3 x 2.72 x 2.93 m" in system
    assert '"position.z": "frames 1-73, 7 keys"' in user and '"frame_end": 73' in user and "Suzanne" in user


# ---------- the three acceptance prompts, sequentially, one scene (MOCK model + SIMULATED engine) ----------

def test_phase1_sequence_mock_sim():
    model, m = RuleMockModel(), None
    r = plan("Create a red cube beside a cabin.", model, manifest=m)
    m = run_sim(m, r.plan)
    s1 = by_id(m)
    assert sorted(s1) == ["cabin_1", "cube_1"] and s1["cube_1"]["color"] == RED
    assert all(c.ok for c in postcheck.run(r.plan, None, m))

    r = plan("Change the red cube to blue.", model, manifest=m)
    assert r.plan["operations"] == [{"op": "modify", "id": "cube_1", "set": {"color": BLUE}}]
    before, m = m, run_sim(m, r.plan)
    s2 = by_id(m)
    assert sorted(s2) == ["cabin_1", "cube_1"] and s2["cube_1"]["color"] == BLUE and s2["cabin_1"] == s1["cabin_1"]
    assert all(c.ok for c in postcheck.run(r.plan, before, m))

    r = plan("Add a tree and make the cube bounce three times.", model, manifest=m)
    assert [o["op"] for o in r.plan["operations"]] == ["add", "bounce"]
    before, m = m, run_sim(m, r.plan)
    s3 = by_id(m)
    assert sorted(s3) == ["cabin_1", "cube_1", "tree_1"] and s3["cabin_1"] == s1["cabin_1"]
    assert all(c.ok for c in postcheck.run(r.plan, before, m)), [c.line() for c in postcheck.run(r.plan, before, m)]


def test_mock_multi_step_with_pronoun():
    r = plan("Create a cube, put a tree next to it, and make the cube bounce three times.", RuleMockModel())
    ops = r.plan["operations"]
    assert [(o["op"], o["id"]) for o in ops] == [("add", "cube_1"), ("add", "tree_1"), ("bounce", "cube_1")]
    gap = abs(ops[1]["position"][0] - ops[0]["position"][0]) - 0.5 - 0.75
    assert 0.5 <= gap <= 1.2 and ops[2]["cycles"] == 3


# ---------- bridge inspection ----------

def test_inspect_round_trip_via_fake_worker(tmp_path):
    inbox, outbox, state = tmp_path / "in", tmp_path / "out", tmp_path / "state.json"
    inbox.mkdir(), outbox.mkdir()
    state.write_text(json.dumps({"scene_version": 4, "scene_objects": [
        {"id": "cube_1", "asset": "cube", "color": BLUE, "position": [0, 0, 0]}]}))
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            fake_worker.process_once(inbox, outbox, state)
            time.sleep(0.02)
    t = threading.Thread(target=worker)
    t.start()
    try:
        live = bridge.inspect(timeout=3, inbox=inbox, outbox=outbox)
    finally:
        stop.set()
        t.join()
    assert live["kind"] == "inspect" and live["scene_version"] == 4 and by_id(live)["cube_1"]["color"] == BLUE
    assert not list(inbox.iterdir()) and not list(outbox.iterdir())  # nothing left behind


def test_inspect_timeout_cleans_up(tmp_path):
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    inbox.mkdir(), outbox.mkdir()
    with pytest.raises(bridge.BridgeTimeout):
        bridge.inspect(timeout=0.2, inbox=inbox, outbox=outbox)
    assert not list(inbox.iterdir())
