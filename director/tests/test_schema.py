import copy
import json

import pytest

from director.paths import REPO_ROOT
from director.schema import pretty, validate_plan

FIX = REPO_ROOT / "shared" / "fixtures"
MANIFEST = json.loads((REPO_ROOT / "director/tests/fixtures/scene_manifest.json").read_text())


def load(name):
    return json.loads((FIX / name).read_text())


def test_create_fixture_valid():
    assert validate_plan(load("create_demo.json")) == []


def test_edit_fixture_valid_against_manifest():
    assert validate_plan(load("edit_demo.json"), MANIFEST, expected_mode="edit") == []


def test_edit_fixture_invalid_on_empty_scene():
    errs = validate_plan(load("edit_demo.json"), None)
    assert any("unknown object 'mug_1'" in e for e in errs)


@pytest.mark.parametrize("mutate,needle", [
    (lambda p: p["operations"][0].update(op="magic_animation"), "unknown op 'magic_animation'"),
    (lambda p: p["operations"][0].update(color="red"), "does not match"),
    (lambda p: p["operations"][0].update(color="#12345"), "does not match"),
    (lambda p: p["operations"][0].update(asset="dragon"), "'dragon' is not one of"),
    (lambda p: p["operations"][0].update(code="import os"), "Additional properties"),
    (lambda p: p.update(mode="destroy"), "'destroy' is not one of"),
    (lambda p: p["operations"][1].update(id="tree_1", asset="tree"), "duplicate add id 'tree_1'"),
    (lambda p: p["operations"][1].update(id="cup_1"), "must start with its asset name"),
    (lambda p: p["operations"][0].update(position=[1, 2]), "too short"),
    (lambda p: p["operations"][0].update(scale=-1), "less than or equal to the minimum"),
    (lambda p: p.update(operations=[]), "should be non-empty"),
])
def test_invalid_create_plans_rejected(mutate, needle):
    plan = load("create_demo.json")
    mutate(plan)
    errs = validate_plan(plan)
    assert errs, "expected errors"
    assert any(needle in e for e in errs), errs


def test_add_existing_id_rejected():
    errs = validate_plan(load("create_demo.json"), MANIFEST)
    assert any("already exists in the scene" in e for e in errs)


def test_hallucinated_id_rejected():
    plan = load("edit_demo.json")
    plan["operations"][0]["id"] = "mug_7"
    errs = validate_plan(plan, MANIFEST)
    assert any("unknown object 'mug_7'" in e and "mug_1, tree_1" in e for e in errs)


def test_delete_and_recreate_rejected_in_edit():
    plan = {"schema_version": 1, "request_id": "abc123", "mode": "edit", "summary": "x", "operations": [
        {"op": "delete", "id": "mug_1"},
        {"op": "add", "id": "mug_2", "asset": "mug", "color": "#0000FF", "position": [1, 0, 0]}]}
    assert any("re-create" in e for e in validate_plan(plan, MANIFEST))


def test_op_after_delete_rejected():
    plan = {"schema_version": 1, "request_id": "abc123", "mode": "edit", "summary": "x", "operations": [
        {"op": "delete", "id": "mug_1"}, {"op": "modify", "id": "mug_1", "set": {"scale": 2}}]}
    assert any("deleted earlier" in e for e in validate_plan(plan, MANIFEST))


@pytest.mark.parametrize("start,end,ok", [(1, 48, True), (10, 10, False), (20, 5, False), (0, 10, False)])
def test_animation_ranges(start, end, ok):
    plan = {"schema_version": 1, "request_id": "abc123", "mode": "edit", "summary": "spin", "operations": [
        {"op": "animate", "id": "mug_1", "property": "rotation", "start_frame": start, "end_frame": end,
         "to": [0, 0, 360]}]}
    assert (validate_plan(plan, MANIFEST) == []) is ok


def test_animate_new_object_in_same_plan_ok():
    plan = load("create_demo.json")
    plan["operations"].append({"op": "animate", "id": "mug_1", "property": "position", "start_frame": 1,
                              "end_frame": 24, "to": [1, 0, 2]})
    assert validate_plan(plan) == []


def test_empty_modify_rejected():
    plan = load("edit_demo.json")
    plan["operations"][0]["set"] = {}
    assert validate_plan(plan, MANIFEST)


def test_clarify_rules():
    base = {"schema_version": 1, "request_id": "abc123", "mode": "clarify", "summary": "no dragons",
            "operations": []}
    assert any("'message' is a required property" in e for e in validate_plan(base))
    assert validate_plan({**base, "message": "Dragons are not supported."}) == []
    bad = copy.deepcopy(load("create_demo.json"))
    assert validate_plan({**base, "message": "x", "operations": bad["operations"]})


def test_expected_mode_mismatch():
    errs = validate_plan(load("create_demo.json"), None, expected_mode="edit")
    assert any("expected 'edit'" in e for e in errs)


def test_pretty_is_text():
    out = pretty(load("create_demo.json"))
    assert "add" in out and "mug_1" in out
