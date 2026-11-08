# TinyDirector — notes for Claude Code

Natural language → NVIDIA Nemotron plans a constrained JSON scene plan → plan is validated → Blender executes it →
the real scene state read back from Blender feeds the next request (so "make only the mug blue" touches only the mug).
HackUTD 2026 project.

## Hard rules
- `shared/schema/*.json` and `shared/fixtures/*.json` are the contract between the director and the Blender engine. Do not change them without the team agreeing; fix code instead.
- `director/` = Nemotron planning lane. `blender_engine/` = Blender executor (runs inside Blender's Python: no jsonschema/openai there).
- Plans are DATA. Never eval/exec model output. Max ONE corrective retry, then fail without executing.
  Sole exception (team-approved): lesson builder code runs ONLY via `blender_engine/sandbox.py` (AST allowlist,
  no imports / attribute access) inside a separate headless Blender (`lesson_runner.py`) with a timeout; the user's
  Blender only appends data from a verified `lesson.blend`.
- Never print, log, or commit API keys. Keys live in the gitignored `.env`.
- Tests must pass offline: `python -m pytest director/tests -q`.
- Anything mocked or simulated must be labeled MOCK / SIMULATED in output. Never present it as a live Nemotron call or real Blender.

## Layout
- `director/` — runs in a normal venv (`openai`, `jsonschema`, `pytest`).
  - `cli.py` entry point (`python -m director.cli`); `planner.py` text+manifest → Nemotron → plan (1 retry);
    `schema.py` JSON Schema + scene-aware rules (ids exist, no dupes, id prefix = asset, frame ranges);
    `nemotron_client.py` OpenAI-compatible NIM client; `postcheck.py` checks each op against Blender's read-back
    (never trust the model's word); `assets_info.py` measured asset sizes for the prompt; `verifier.py` Nemotron VL checks the Blender render and may
    propose one fix instruction (which goes back through planner + validator); `bridge.py` inbox/outbox file bridge;
    `engine_sim.py` pure-Python reference executor (SIMULATED); `mock_model.py` offline models (MOCK);
    `trace.py` per-request trace saved to `shared/state/traces/`; `paths.py` repo paths + dependency-free `.env` loader.
  - `dev/fake_worker.py` — SIMULATED stand-in for the Blender worker on the bridge.
  - `dev/phase1_e2e.py` — acceptance run: 3 prompts in one headless Blender scene via a private bridge folder.
- `blender_engine/` — pure `bpy`/`bmesh`, stdlib only. `worker.py` timer loop (polls `shared/inbox` every 0.25 s;
  `-- --loop` for headless, `--save PATH`), `engine.py` applies ops all-or-nothing + reads state back (incl.
  keyframes), `assets.py` 14 procedural assets (cabin included), `phase1_check.py` / `inspect_blend.py` real-Blender checks, `snapshot.py` 640px JPEG for the
  visual check (`TD_SNAPSHOT=0` disables), `headless_check.py` self-test, `run_blender.ps1` Windows-only launcher.
- `shared/` — `schema/` (plan + result v1), `fixtures/`, `ENGINE_CONTRACT.md` (Blender-side semantics; `engine_sim.py`
  is the reference implementation). `inbox/`, `outbox/`, `state/` are runtime-only and gitignored.

## Asset library (Phase 2)
`assets/catalog.json` (sources, CC0 licenses, params) -> `python assets/fetch.py` -> `assets/cache/`;
`blender -b --factory-startup --python assets/build_library.py` -> `assets/library/*.blend` + `index.json` (both gitignored).
Worker with `TD_ASSET_LIBRARY=1` / `-- --library` renders `cabin/tree/campfire/rock/lamp` as library meshes
(`blender_engine/library.py`); plans/contract unchanged; `color` sets the `TD_Tint` node. Docs: `docs/ASSETS.md`,
`docs/PHASE2.md`. Visual experiments: `experiments/winter_diorama.py` (writes to gitignored `experiments/out/`).
Blender MCP is NOT used at runtime (arbitrary code exec, telemetry on by default); see docs/PHASE2.md.
Test harness gotcha: `--factory-startup` scenes contain the default Cube at the origin; start from
`read_factory_settings(use_empty=True)`. `mesh.materials.clear()` resets every face to slot 0.

## Circuit tutor (Phase 3: "Why does an LED need a resistor?")
`python -m director.tutor` (one turn: `--text`; offline: `--simulate --mock`). Nemotron only picks ONE tool
(`teach_led_resistor` / `set_resistance` / `explain` / `clarify`, validated, 1 retry) and explains facts;
`director/circuit/model.py` is the ONLY source of numbers (I = (Vs - Vf)/R, 9 V, 2 V LED, 330 ohm default);
`display.py` maps them to labels / marker speed / glow (illustrative, proportional to current); `agent.py` rejects
explanation numbers not in the facts (unit-aware) -> 1 retry -> template; `check.py` verifies Blender's read-back.
Bridge: `{"kind": "circuit"}` requests (`shared/schema/circuit_request_v1.json`, additive) -> `blender_engine/circuit.py`
(build / update on stable ids `battery_1 resistor_1 led_1 wire_1..3 flow_1 board_1 label_*`; flow = Geometry Nodes
markers, `modifiers["Flow"].properties.inputs.Socket_N.value` in Blender 5.x). Docs: `docs/CIRCUIT_TUTOR.md`.
Checks: `blender -b --factory-startup --python blender_engine/circuit_check.py`, `python -m director.dev.circuit_e2e [--mock]`.
Scene title follows the turn (`display.title_for`): lesson question / "What if the resistor is X?" / the learner's question.

## Multi-agent lessons (any small electronics question; docs/MULTI_AGENT.md)
Tutor tool `teach_topic` or `python -m director.lesson --text "..." [--send] [--builder auto|openai|deterministic]
[--no-cache] [--no-vision] [--mock]`. Nemotron `director_agent.py` writes `lesson_spec_v1` (additive schema;
`spec.py` validates refs/pins/flows, no bare numbers, lint: calc `shown_on` parts present, LED series resistor, no
flow through a capacitor) -> `calculators.py` fill `{calc.output}` -> OpenAI `builder.py` (OPENAI_API_KEY in `.env`)
writes toolkit code (`blender_engine/toolkit.py` API, names in `builder.TOOLKIT_NAMES`, test keeps them equal) ->
`pipeline.run_blender` (separate `blender -b`) -> `verify.py` (pins within 2 cm, flow current + speed, texts, layout,
Nemotron VL) -> 1 repair -> `fallback.py` deterministic builder (layout = warnings). Cache: `<shared>/state/lesson_cache/`.
Worker `{"kind": "lesson"}` -> `lesson_loader.py` appends the TD_Lesson collection (path must be a cache
`lesson.blend`), director re-verifies the live read-back. Circuit build clears the lesson and vice versa.

## Poyo studio: scene-first + voice (frontend/, director/server.py, director/studio.py; docs/STUDIO.md)
Blender's viewport is the stage (worker `-- --stage`: full-window camera view, no panels/overlays, camera frame fitted,
opaque passepartout; `--rendered` = EEVEE). Poyo = small companion browser window beside it (option A; a panel inside
Blender was rejected: no mic capture in Blender's Python, approved SVG art/animations would need redrawing). Main view:
Poyo (`frontend/poyo.svg`, approved art: keep it and its animations; keep ids poyo-body/eyes/mouth), mic, subtitle,
stage title + calculated chips, status pill; drawer (hidden by default, `/#activity|chat|knowledge|type`) = activity,
conversation + grounding, knowledge map, Type tab (text fallback). `python -m director.studio [--blender] [--mock
--simulate] [--no-voice]`; `python -m director.server` serves the same page (stdlib http.server, localhost only).
Voice = transport only (`director/voice.py`, OpenAI `gpt-4o-mini-transcribe` / `gpt-4o-mini-tts`, OPENAI_API_KEY;
`FakeVoice` = MOCK): STT -> wake word stripped -> the normal turn; Poyo speaks the verified reply. No Realtime API
(would answer by itself). `/api/tts` only speaks tutor replies + fixed PHRASES (403 otherwise); TTS cache in
`<shared>/state/tts_cache/`.
Turn tools: `adjust_resistance` (`model.step_resistance`, E6 x/÷3), `focus_camera` (display only), `explain` sends
NOTHING to Blender. Build/update are sent right after the calculation (caption = calculated change), then a
`direct` caption request sets the explanation. Turn events carry `stage={"kind","cues"}` (`director/stage.py`:
deterministic keyword cues per sentence/clause -> focus target + caption); the browser plays cues in sync with TTS
via `/api/direct`. Stage direction: `blender_engine/direction.py` (`{"kind":"direct"}`, additive
`shared/schema/direct_request_v1.json`: focus battery|resistor|led|current|overview|part id, caption; camera glide by
bpy.app.timers, no keyframes; halo ring; applied=0). `events.py`: emit(stage, status, title, detail, /, **data);
REAL events only (start right before a step runs); `listen` events belong to the upcoming turn. `context` carries the
last explanation; follow-ups (`tutor.FOLLOW_UP_RE`) re-explain it and keep the scene title.
API: GET /api/state, POST /api/turn {text} (409 busy), POST /api/voice <audio> (422 nothing heard, 503 no voice),
POST /api/tts {text}, POST /api/direct {focus, caption}, POST /api/reset, GET /api/events (SSE, ?since=),
GET /api/snapshot/<name>, GET /api/knowledge. Tests: test_server.py, test_stage_voice.py.

## Knowledge base (P1: director/knowledge/, docs/KNOWLEDGE.md)
`seed.json` (reviewed, in git) -> SQLite `<shared>/state/knowledge.sqlite` (`TD_KNOWLEDGE_DB`; auto-rebuilt when the
seed changes; tests use a tmp DB via conftest). `python -m director.knowledge build|stats|query "..."|graph`.
Tables: concepts (demo = interactive/lesson/explanation_only, DERIVED from templates; parallel = explanation_only),
aliases, relations (requires/is_a/uses/contrasts), facts (status verified = calculator re-run at build / sourced /
assumption; every fact has a source), misconceptions (trigger regex on the question, claim regex on model text),
templates (LED tutor + reviewed specs in `lesson/examples.py`), checks (ids only; `registry.py` is the allowlist).
Per turn: `knowledge` event -> `<knowledge>` hint to the tool selector (new tool `explain_concept`, id validated) ->
explainer/director get facts + misconceptions in the SYSTEM prompt; numbers are checked against calculated FACTS only
(never retrieved knowledge); `claims.find_wrong_claims` rejects stated misconceptions (1 retry -> template). Lesson
director uses the matched template spec as its example. Follow-ups on a generated lesson -> `explain_topic` (its own
calculations). Turn events carry `grounding` (numbers calculated/cited/none + retrieved facts): retrieval is NOT
verification; never label sourced facts as verified.

## Ops
`add`, `modify` (shifts an animated channel instead of wiping it), `delete`, `animate` (2 keys; start = value the
object actually has at start_frame, so segments chain), `bounce` (cycles x Z hop, ends at start height). Timeline is
fitted to director keyframes. Positions are the object's BASE (z=0 = on the ground).
Blender 5.x has no `Action.fcurves`: use `engine.fcurves(obj)` (channelbag via `bpy_extras.anim_utils`).

## Bridge protocol
Director atomically writes `shared/inbox/<request_id>.json` (.tmp + `os.replace`); worker ignores `*.tmp`, applies,
writes `shared/outbox/<request_id>.json` (+ `<request_id>.jpg` snapshot), deletes the inbox file. `{"kind":"inspect"}`
requests return the live scene without applying anything; `--send` inspects before every plan (Blender is the
source of truth). `TD_SHARED_DIR` relocates inbox/outbox/state for both sides. If the user has Blender open on
`shared/`, never send test jobs there: use `phase1_e2e` (private folder) or set `TD_SHARED_DIR`.
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
python -m director.cli --inspect                       # live scene JSON
python -m director.nvidia_smoke / director.vision_smoke   # live API checks
blender -b --python blender_engine/phase1_check.py     # Phase 1 engine checks in real Blender
python -m director.dev.phase1_e2e [--mock]             # Phase 1 end-to-end; OVERWRITES docs/phase1_demo.blend unless --save PATH
python -m director.dev.library_e2e [--mock]            # asset-library end-to-end (needs built library)
python -m director.tutor                               # circuit tutor, interactive (Blender worker running)
python -m director.dev.circuit_e2e [--mock]            # circuit lesson end-to-end, 3 prompts, private bridge
python -m director.lesson --text "..." [--send]        # multi-agent lesson (Nemotron director + OpenAI builder)
python -m director.server [--mock --simulate]           # Poyo companion UI at http://127.0.0.1:8765
python -m director.studio --blender                    # Blender stage (-- --stage) + Poyo voice companion
python -m director.dev.voice_e2e [--mock]              # voice demo end-to-end (synthesized speech, private bridge)
blender -b --factory-startup --python blender_engine/direction_check.py   # camera focus / halo / caption checks
python -m director.knowledge query "..."               # knowledge-base retrieval (what Nemotron is given)
```
Flags: `--mock` (offline model), `--reset`, `--mode auto|create|edit`, `--manifest PATH`, `--json`, `-v`,
`--no-verify`, `--max-corrections N`, `--timeout S`.
Exit codes: 0 ok/clarify · 1 model call failed · 2 config · 3 plan invalid after retry · 4 bridge timeout · 5 execution error / post-check failed.

## Config (env or `.env`; real env vars win)
`NVIDIA_API_KEY`, `NEMOTRON_MODEL` (default `nvidia/nemotron-3.5-super-vl-120b-a12b`), `NEMOTRON_BASE_URL`
(default `https://integrate.api.nvidia.com/v1`), `NEMOTRON_THINKING` (0), `NEMOTRON_TIMEOUT` (90), `NEMOTRON_VL_MODEL`,
`TINYDIRECTOR_ROOT` (worker repo-root override), `TD_SNAPSHOT`, `TD_SHARED_DIR`.
Lesson builder: `OPENAI_API_KEY` (empty -> deterministic builder, labeled), `OPENAI_BUILDER_MODEL` (gpt-5-mini),
`OPENAI_BASE_URL`, `OPENAI_TIMEOUT` (180), `OPENAI_REASONING_EFFORT`; `BLENDER` = path to blender if not on PATH.
Voice: `OPENAI_STT_MODEL` (gpt-4o-mini-transcribe), `OPENAI_TTS_MODEL` (gpt-4o-mini-tts), `OPENAI_TTS_VOICE` (coral).
