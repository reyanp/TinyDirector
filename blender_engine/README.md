# blender_engine/ — executes plans inside Blender

Tested on Blender 5.0.1 (should work on 4.x). Pure `bpy`/`bmesh`, no add-on install and no extra packages.

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
- `engine.py` — applies ops, re-validates each op against the live scene, reads state back from Blender objects.
- `snapshot.py` — after each job, renders a 640px JPEG (Workbench) from an auto-framed camera into `shared/outbox/<id>.jpg` for the visual check. `TD_SNAPSHOT=0` disables it.
- `assets.py` — procedural meshes for the 13 assets (one object per id, material slot 0 = the plan's color).
- `headless_check.py` — `blender -b --python blender_engine/headless_check.py -- --render preview.png`

Contract: `shared/ENGINE_CONTRACT.md`. Plans are data; unknown ops/assets/ids are reported as errors, never executed.
