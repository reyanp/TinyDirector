import json

import pytest

from director.mock_model import RuleMockModel, ScriptedModel
from director.paths import REPO_ROOT
from director.planner import PlanError, build_messages, extract_json, plan

MANIFEST = json.loads((REPO_ROOT / "director/tests/fixtures/scene_manifest.json").read_text())
CREATE = (REPO_ROOT / "shared/fixtures/create_demo.json").read_text()
EDIT = (REPO_ROOT / "shared/fixtures/edit_demo.json").read_text()


def test_extract_plain():
    assert extract_json(CREATE)["mode"] == "create"


def test_extract_fenced_with_prose_and_think():
    raw = "<think>the user wants a mug {not json}</think>Sure! Here you go:\n```json\n" + CREATE + "\n```\nEnjoy."
    assert extract_json(raw)["operations"][0]["id"] == "tree_1"


def test_extract_braces_inside_strings():
    raw = 'x {"a": "has } and { inside", "b": 1} trailing'
    assert extract_json(raw) == {"a": "has } and { inside", "b": 1}


@pytest.mark.parametrize("bad", ["", "no json here", '{"cut": "off'])
def test_extract_failures(bad):
    with pytest.raises(ValueError):
        extract_json(bad)


def test_create_first_try():
    r = plan("Create a red mug next to a tree", ScriptedModel([CREATE]), mode="create")
    assert r.attempts == 1 and r.plan["mode"] == "create"
    assert r.plan["request_id"] != "fixture_create_001"  # we assign request ids, not the model


def test_edit_targets_existing_id():
    model = ScriptedModel([EDIT])
    r = plan("Change only the mug to blue", model, manifest=MANIFEST, mode="edit")
    assert r.plan["operations"] == [{"op": "modify", "id": "mug_1", "set": {"color": "#3B6FD9"}}]
    prompt = model.calls[0][1]["content"]
    assert "mug_1" in prompt and "tree_1" in prompt and "Change only the mug to blue" in prompt
    assert '"mug": "mug_2"' in prompt  # next free id hint


def test_retry_once_then_success_with_errors_fed_back():
    bad = json.loads(EDIT)
    bad["operations"][0]["id"] = "mug_7"
    model = ScriptedModel(["Sure thing! " + json.dumps(bad), EDIT])
    r = plan("make the mug blue", model, manifest=MANIFEST, mode="edit")
    assert r.attempts == 2
    feedback = model.calls[1][-1]["content"]
    assert "unknown object 'mug_7'" in feedback
    assert any(s["stage"] == "validate" and "rejected attempt 1" in s["detail"] for s in r.trace.steps)


def test_fails_after_one_retry_and_never_three_calls():
    model = ScriptedModel(["not json", '{"mode": "create"}', CREATE])
    with pytest.raises(PlanError) as exc:
        plan("make stuff", model)
    assert len(model.calls) == 2
    assert exc.value.errors


def test_python_in_plan_is_rejected():
    evil = json.loads(CREATE)
    evil["operations"][0]["python"] = "__import__('os').system('rm -rf /')"
    with pytest.raises(PlanError):
        plan("x", ScriptedModel([json.dumps(evil), json.dumps(evil)]))


def test_mode_mismatch_triggers_retry():
    with pytest.raises(PlanError) as exc:
        plan("make the mug blue", ScriptedModel([CREATE, CREATE]), manifest=None, mode="edit")
    assert any("expected 'edit'" in e for e in exc.value.errors)


def test_prompt_contains_schema_and_assets():
    msgs = build_messages("hi", None, "create")
    assert '"scene_plan_v1.json"' in msgs[0]["content"] or "scene_plan_v1" in msgs[0]["content"]
    assert "campfire" in msgs[0]["content"]


# --- rule-based mock (used by --mock) ---------------------------------------

def test_mock_create():
    r = plan("Create a small tree and a red mug", RuleMockModel(), mode="create")
    assets = sorted(o["asset"] for o in r.plan["operations"])
    assert assets == ["mug", "tree"] and not r.live


def test_mock_edit_only_mug():
    r = plan("Change only the mug to blue", RuleMockModel(), manifest=MANIFEST, mode="edit")
    assert r.plan["operations"] == [{"op": "modify", "id": "mug_1", "set": {"color": "#3B6FD9"}}]


def test_mock_dragon_clarifies():
    r = plan("Make an enormous dragon perform somersaults", RuleMockModel())
    assert r.plan["mode"] == "clarify" and r.plan["operations"] == []
