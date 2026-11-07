# TinyDirector — notes for Claude Code

Natural language → NVIDIA Nemotron plans a constrained JSON scene plan → plan is validated → Blender executes it →
the real scene state read back from Blender feeds the next request (so "make only the mug blue" touches only the mug).
HackUTD 2026 project.

## Hard rules
- `shared/schema/*.json` and `shared/fixtures/*.json` are the contract between the director and the Blender engine. Do not change them without the team agreeing; fix code instead.
- `director/` = Nemotron planning lane. `blender_engine/` = Blender executor (runs inside Blender's Python: no jsonschema/openai there).
- Plans are DATA. Never eval/exec model output. Max ONE corrective retry, then fail without executing.
- Never print, log, or commit API keys. Keys live in the gitignored `.env`.
- Tests must pass offline: `python -m pytest director/tests -q`.
- Anything mocked or simulated must be labeled MOCK / SIMULATED in output. Never present it as a live Nemotron call or real Blender.

## Layout
- `director/` — runs in a normal venv (`openai`, `jsonschema`, `pytest`).
  - `cli.py` entry point (`python -m director.cli`); `planner.py` text+manifest → Nemotron → plan (1 retry);
    `schema.py` JSON Schema + scene-aware rules (ids exist, no dupes, id prefix = asset, frame ranges);
    `nemotron_client.py` OpenAI-compatible NIM client; `verifier.py` Nemotron VL checks the Blender render and may
    propose one fix instruction (which goes back through planner + validator); `bridge.py` inbox/outbox file bridge;
    `engine_sim.py` pure-Python reference executor (SIMULATED); `mock_model.py` offline models (MOCK);
    `trace.py` per-request trace saved to `shared/state/traces/`; `paths.py` repo paths + dependency-free `.env` loader.
  - `dev/fake_worker.py` — SIMULATED stand-in for the Blender worker on the bridge.
- `blender_engine/` — pure `bpy`/`bmesh`, stdlib only. `worker.py` timer loop (polls `shared/inbox` every 0.25 s),
  `engine.py` applies ops + reads state back, `assets.py` 13 procedural assets, `snapshot.py` 640px JPEG for the
  visual check (`TD_SNAPSHOT=0` disables), `headless_check.py` self-test, `run_blender.ps1` Windows-only launcher.
- `shared/` — `schema/` (plan + result v1), `fixtures/`, `ENGINE_CONTRACT.md` (Blender-side semantics; `engine_sim.py`
  is the reference implementation). `inbox/`, `outbox/`, `state/` are runtime-only and gitignored.

## Bridge protocol
Director atomically writes `shared/inbox/<request_id>.json` (.tmp + `os.replace`); worker ignores `*.tmp`, applies,
writes `shared/outbox/<request_id>.json` (+ `<request_id>.jpg` snapshot), deletes the inbox file. Last real result is
`shared/state/latest_manifest.json` (simulator uses `simulated_manifest.json`); edits default to it unless `--manifest`.
Objects are tracked by the `director_id` custom property. `scene_objects` must be read back from Blender, never echoed.

## Commands (Linux, from repo root)
```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r director/requirements.txt
cp .env.example .env                                   # add nvapi-... key (NOT a Brev bak- key)

python -m pytest director/tests -q                     # offline tests
blender -b --python blender_engine/headless_check.py -- --render /tmp/preview.png   # engine self-test, no GUI
blender --python blender_engine/worker.py              # Blender GUI + worker (add `-- --keep` to keep old scene)
python -m director.dev.fake_worker --reset             # SIMULATED worker instead of Blender

python -m director.cli --text "..." --dry-run          # plan + validate only (default)
python -m director.cli --text "..." --simulate         # pure-Python executor
python -m director.cli --text "..." --send --trace     # real Blender via bridge (+ vision verify)
python -m director.cli --send --trace                  # interactive multi-turn
python -m director.nvidia_smoke / director.vision_smoke   # live API checks
```
Flags: `--mock` (offline model), `--reset`, `--mode auto|create|edit`, `--manifest PATH`, `--json`, `-v`,
`--no-verify`, `--max-corrections N`, `--timeout S`.
Exit codes: 0 ok/clarify · 1 model call failed · 2 config · 3 plan invalid after retry · 4 bridge timeout · 5 execution error.

## Config (env or `.env`; real env vars win)
`NVIDIA_API_KEY`, `NEMOTRON_MODEL` (default `nvidia/nemotron-3.5-super-vl-120b-a12b`), `NEMOTRON_BASE_URL`
(default `https://integrate.api.nvidia.com/v1`), `NEMOTRON_THINKING` (0), `NEMOTRON_TIMEOUT` (90), `NEMOTRON_VL_MODEL`,
`TINYDIRECTOR_ROOT` (worker repo-root override), `TD_SNAPSHOT`.
