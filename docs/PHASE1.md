# Phase 1: scene intelligence and animation reliability

## Audit (before changes)
| question | answer |
|---|---|
| Nemotron call | `director/nemotron_client.py`: OpenAI SDK → NVIDIA NIM (`nemotron-3.5-super-vl-120b-a12b`), key from env/`.env` |
| Nemotron → Blender | file bridge: `shared/inbox/<id>.json` → Blender timer worker → `shared/outbox/<id>.json` |
| Blender MCP | not used |
| Code or structured ops | structured JSON ops (`add/modify/delete/animate`) validated against `shared/schema/scene_plan_v1.json`; no model code is ever run |
| Scene access | Blender reads objects back after each job; the director used the *last saved* result file, so it was stale if Blender restarted or was edited by hand. No animation, parent, type or timeline info |
| Modify support | yes (`modify` by `director_id`) |
| Animation | `animate` = 2 keyframes (start → end) |
| Shared contract used | yes, by both sides |

## Root causes
- **Cabin looked like a tent**: there was no cabin asset, and `tent` was the closest supported asset.
- **Bounce jumped once and froze**, confirmed in Blender 5.2:
  - `animate` can only express one A→B segment, so "bounce" became one rise and then a hold until frame 250.
  - Chaining an up and a down segment did not help either. The second segment's start key took the object's *rest* value and overwrote the first segment's peak, so the keys were `[(1,0), (12,0), (24,0)]`: no motion at all.
  - The timeline also stayed 1-250.
- **Blender 5.x** removed `Action.fcurves`. Code reading keyframes now goes through the slot's channelbag.

## Changes
- **Contract (additive; every existing v1 plan and result stays valid)**:
  - New asset `cabin`.
  - New op `bounce {id, cycles, height?, start_frame?, frames_per_cycle?}`.
  - Optional result fields:
    - top level: `op_results`, `scene` timeline, `other_objects`, `kind`
    - per object: `name`, `type`, `parent`, `material`, `animation`
- **Engine** (`blender_engine/engine.py`):
  - Plans are applied all-or-nothing: each op is checked first, with a rollback if Blender raises.
  - `bounce` produces ballistic Z keyframes: ease-out going up, ease-in coming down.
  - `animate` chains from the value the object actually has at `start_frame`.
  - `modify` on an animated channel shifts the curve instead of wiping it.
  - The timeline is fitted to the animation.
  - Full read-back of the scene.
  - Read-only `inspect` requests.
  - Worker `--loop` and `--save` flags for headless runs.
- **Director**:
  - Inspects the live Blender scene before every plan.
  - The prompt now covers bounce, multi-step ordering, real asset sizes, the "z is the base" rule, and animation state.
  - `postcheck.py` checks every op against Blender's read-back and confirms untouched objects did not change.
  - The CLI prints a done/FAIL line for each op, and a failed post-check means exit code 5.
- **Tests**:
  - `director/tests/test_phase1.py`: 18 offline tests.
  - `blender_engine/phase1_check.py`: real Blender, measured frame by frame.
  - `director/dev/phase1_e2e.py`: the full pipeline in a private bridge folder.
  - `docs/phase1_demo.blend`: the saved result of the three prompts.

## Reproduce
```bash
python -m pytest director/tests -q                                   # offline
blender -b --python blender_engine/phase1_check.py                   # engine in real Blender
python -m director.dev.phase1_e2e            # live Nemotron + headless Blender, writes docs/phase1_demo.blend
python -m director.dev.phase1_e2e --mock     # same, offline model (MOCK)
blender docs/phase1_demo.blend               # open, press Space
```

## Known limitations
- Bounce is a fixed-height vertical hop: no squash and stretch, no decay, no travel. `animate` covers linear moves and spins.
- No `loop forever` option. `cycles` is capped at 20, and the last frame is 1000.
- The timeline is fitted to the director's keyframes, which overrides any frame range set by hand.
- Objects the director didn't create are visible to the planner (`other_objects`) but can't be edited.
- Nemotron output is non-deterministic. The validator and post-check catch bad plans, but a plan can still be valid and wrong (for example, odd placement). Only the visual check judges that.
- The simulator interpolates linearly between keys. Key values are exact, but the motion in between is approximate.
