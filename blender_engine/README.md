# blender_engine/ — executes plans inside Blender

Tested on Blender 5.0.1 (should work on 4.x). Pure `bpy`/`bmesh`, no add-on install and no extra packages.

## Run it (Linux / macOS)
```bash
blender --python blender_engine/worker.py              # GUI + worker; add `-- --keep` to keep the old scene
blender -b --python blender_engine/worker.py -- --loop # headless worker (no window)
```

## Run it (Windows)
From the repo root:
```powershell
powershell -ExecutionPolicy Bypass -File blender_engine\run_blender.ps1
```
Blender opens, clears old TinyDirector objects, adds a sun light, and starts polling `shared\inbox` every 0.25 s.
The Blender console prints `[TinyDirector] ... worker running`. Use `-Keep` to keep the previous scene,
or `-Blender "C:\path\to\blender.exe"` if it can't find Blender.

Alternative: open Blender → **Scripting** tab → Open `blender_engine\worker.py` → **Run Script**.
(Window → Toggle System Console shows the `[TinyDirector]` log lines.)

Then, in a second terminal with the venv active:
```powershell
python -m director.cli --send --text "Create a red mug next to a tree" --trace
python -m director.cli --send --text "Change only the mug to blue" --trace
python -m director.cli --send --trace          # interactive session
```
Press **Space** in the viewport to play animations ("make the tree spin").

## Files
- `worker.py` — timer loop: inbox → engine → outbox; writes `shared/state/blender_manifest.json` at startup and after each job.
- `engine.py` — pre-checks every op against the live scene, applies all-or-nothing (rollback on error), and reads
  state back from Blender: transforms, color, material, parent, keyframes per channel, timeline. `bounce` keys a
  ballistic Z hop; `animate` chains from the value the object actually has at `start_frame`. Works with Blender 4.x
  and 5.x actions (5.x removed `Action.fcurves`; we read the slot's channelbag).
- `snapshot.py` — after each job, renders a 640px JPEG (Workbench) from an auto-framed camera into `shared/outbox/<id>.jpg` for the visual check. `TD_SNAPSHOT=0` disables it.
- `assets.py` — procedural meshes for the 14 assets (incl. `cabin`) (one object per id, material slot 0 = the plan's color).
- `headless_check.py` — `blender -b --python blender_engine/headless_check.py -- --render preview.png`
- `phase1_check.py` — the 3 Phase 1 steps in real Blender, plays the timeline and measures the bounce frame by
  frame; `-- --save docs/phase1_demo.blend` writes the demo file.
- `inspect_blend.py` — `blender -b file.blend --python blender_engine/inspect_blend.py` replays a saved file.

Contract: `shared/ENGINE_CONTRACT.md`. Plans are data; unknown ops/assets/ids are reported as errors, never executed.
