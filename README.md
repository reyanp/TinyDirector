# TinyDirector (HackUTD 2026)

Talk to Blender. NVIDIA Nemotron reads the real scene, plans a constrained set of tool actions as JSON, the plan is validated, Blender executes it, and the resulting scene state feeds the next request. So "turn just the mug blue" changes the mug and nothing else.

- `director/` — Nemotron planner, validator, CLI, inbox/outbox bridge ([director/README.md](director/README.md))
- `shared/schema/` — the plan + result contract; `shared/ENGINE_CONTRACT.md` — what the Blender side implements
- `blender_engine/` — Blender executor: worker, procedural assets, Windows launcher ([blender_engine/README.md](blender_engine/README.md))

Quick start: `python -m pytest director/tests -q`, then see director/README.md.

## Demo in two terminals (Windows)
```powershell
powershell -ExecutionPolicy Bypass -File blender_engine\run_blender.ps1     # terminal 1 (opens Blender)
python -m director.cli --send --text "Create a red mug next to a tree" --trace   # terminal 2
python -m director.cli --send --text "Change only the mug to blue" --trace
```

## Demo in two terminals (Linux)
```bash
blender --python blender_engine/worker.py                                      # terminal 1
source .venv/bin/activate                                                      # terminal 2
python -m director.cli --send --text "Create a red cube beside a cabin." --trace
python -m director.cli --send --text "Change the red cube to blue." --trace
python -m director.cli --send --text "Add a tree and make the cube bounce three times." --trace
```
Press Space in Blender's viewport to play. `docs/phase1_demo.blend` is a saved result of exactly these three
prompts (regenerate with `python -m director.dev.phase1_e2e`). Phase 1 notes: [docs/PHASE1.md](docs/PHASE1.md).

## Asset library + polished renders (Phase 2)
![winter diorama](docs/phase2/winter_diorama.png)

CC0 Poly Haven assets and stylized library meshes, used through the same Nemotron plans. See [docs/PHASE2.md](docs/PHASE2.md) and [docs/ASSETS.md](docs/ASSETS.md).
