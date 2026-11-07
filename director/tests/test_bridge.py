import json
import threading
import time

import pytest

from director import bridge
from director.cli import main as cli_main
from director.dev import fake_worker
from director.engine_sim import apply_plan, make_result
from director.paths import REPO_ROOT

CREATE = json.loads((REPO_ROOT / "shared/fixtures/create_demo.json").read_text())
EDIT = json.loads((REPO_ROOT / "shared/fixtures/edit_demo.json").read_text())


@pytest.fixture
def dirs(tmp_path):
    inbox, outbox = tmp_path / "inbox", tmp_path / "outbox"
    inbox.mkdir(), outbox.mkdir()
    return inbox, outbox, tmp_path / "state.json"


def test_simulator_create_then_edit_preserves_tree():
    objs, n, errs = apply_plan([], CREATE)
    assert n == 2 and not errs
    after, n2, _ = apply_plan(objs, EDIT)
    by = {o["id"]: o for o in after}
    assert by["mug_1"]["color"] == "#3B6FD9"
    assert by["tree_1"] == {o["id"]: o for o in objs}["tree_1"]


def test_submit_is_atomic_and_refuses_reuse(dirs):
    inbox, outbox, _ = dirs
    plan = {**CREATE, "request_id": "req_abc123"}
    path = bridge.submit(plan, inbox, outbox)
    assert path.name == "req_abc123.json" and json.loads(path.read_text()) == plan
    assert not list(inbox.glob("*.tmp"))
    with pytest.raises(FileExistsError):
        bridge.submit(plan, inbox, outbox)


def test_wait_reads_stub_result(dirs):
    inbox, outbox, _ = dirs
    plan = {**CREATE, "request_id": "req_stub01"}
    bridge.submit(plan, inbox, outbox)

    def responder():
        time.sleep(0.3)
        bridge.atomic_write_json(outbox / "req_stub01.json", make_result("req_stub01", 0, [], plan))

    threading.Thread(target=responder).start()
    result = bridge.wait_for_result("req_stub01", timeout=3, poll=0.05, outbox=outbox, inbox=inbox)
    assert result["status"] == "ok" and [o["id"] for o in result["scene_objects"]] == ["tree_1", "mug_1"]


def test_timeout_message_is_actionable(dirs):
    inbox, outbox, _ = dirs
    bridge.submit({**CREATE, "request_id": "req_slow01"}, inbox, outbox)
    with pytest.raises(bridge.BridgeTimeout) as exc:
        bridge.wait_for_result("req_slow01", timeout=0.3, poll=0.05, outbox=outbox, inbox=inbox)
    msg = str(exc.value)
    assert "still sitting in the inbox" in msg and "Blender is open" in msg


def test_malformed_result_rejected(dirs):
    inbox, outbox, _ = dirs
    (outbox / "req_bad001.json").write_text(json.dumps({"status": "ok"}))
    with pytest.raises(bridge.BridgeResultError):
        bridge.wait_for_result("req_bad001", timeout=1, poll=0.05, outbox=outbox, inbox=inbox)


def test_fake_worker_two_turns(dirs):
    inbox, outbox, state = dirs
    bridge.submit({**CREATE, "request_id": "turn_one_1"}, inbox, outbox)
    assert fake_worker.process_once(inbox, outbox, state) == 1
    r1 = json.loads((outbox / "turn_one_1.json").read_text())
    bridge.submit({**EDIT, "request_id": "turn_two_2"}, inbox, outbox)
    fake_worker.process_once(inbox, outbox, state)
    r2 = json.loads((outbox / "turn_two_2.json").read_text())
    assert r2["scene_version"] == r1["scene_version"] + 1 == 2
    tree1 = next(o for o in r1["scene_objects"] if o["id"] == "tree_1")
    assert next(o for o in r2["scene_objects"] if o["id"] == "tree_1") == tree1
    assert next(o for o in r2["scene_objects"] if o["id"] == "mug_1")["color"] == "#3B6FD9"
    assert not list(inbox.iterdir())


def test_fake_worker_rejects_invalid_plan(dirs):
    inbox, outbox, state = dirs
    bridge.submit({**EDIT, "request_id": "bad_edit_1"}, inbox, outbox)  # mug_1 doesn't exist yet
    fake_worker.process_once(inbox, outbox, state)
    r = json.loads((outbox / "bad_edit_1.json").read_text())
    assert r["status"] == "error" and r["applied"] == 0


def test_cli_exit_codes(capsys, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr("director.paths.load_env", lambda *a, **k: None)
    monkeypatch.setattr("director.cli.paths.load_env", lambda *a, **k: None)
    assert cli_main(["--text", "make a mug"]) == 2
    monkeypatch.setenv("NVIDIA_API_KEY", "bak-notthekind")
    assert cli_main(["--text", "make a mug"]) == 2
    assert cli_main(["--mock", "--text", "Create a red mug", "--dry-run"]) == 0
    assert cli_main(["--mock", "--manifest", "nope.json", "--text", "x"]) == 2
