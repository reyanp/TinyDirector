import json

from director.mock_model import ScriptedModel
from director.paths import REPO_ROOT
from director.verifier import MockVerifierModel, build_messages, verify

PLAN = json.loads((REPO_ROOT / "shared/fixtures/edit_demo.json").read_text())
RESULT = json.loads((REPO_ROOT / "director/tests/fixtures/scene_manifest.json").read_text())
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")


def _img(tmp_path):
    p = tmp_path / "snap.png"
    p.write_bytes(PNG)
    return p


def test_message_has_text_and_image(tmp_path):
    msgs = build_messages("make the mug blue", PLAN, RESULT, _img(tmp_path))
    parts = msgs[0]["content"]
    assert parts[0]["type"] == "text" and "make the mug blue" in parts[0]["text"] and "mug_1" in parts[0]["text"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_mismatch_verdict_parsed(tmp_path):
    reply = {"matches": False, "confidence": 0.8, "observations": "mug is red", "issues": ["mug still red"],
             "fix_request": "make mug_1 blue"}
    v = verify("make the mug blue", PLAN, RESULT, _img(tmp_path), MockVerifierModel(reply))
    assert not v.matches and v.fix_request == "make mug_1 blue" and v.confidence == 0.8 and not v.live


def test_garbage_verdict_never_triggers_action(tmp_path):
    v = verify("x", PLAN, RESULT, _img(tmp_path), ScriptedModel(["I think it looks nice!"]))
    assert v.matches and v.confidence == 0.0 and v.fix_request == ""


def test_confidence_clamped(tmp_path):
    v = verify("x", PLAN, RESULT, _img(tmp_path), MockVerifierModel({"matches": True, "confidence": 7}))
    assert v.confidence == 1.0
