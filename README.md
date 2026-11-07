# TinyDirector (HackUTD 2026)

Talk to Blender. NVIDIA Nemotron reads the real scene, plans a constrained set of tool actions as JSON, the plan is validated, Blender executes it, and the resulting scene state feeds the next request. So "turn just the mug blue" changes the mug and nothing else.

- `director/` — Nemotron planner, validator, CLI, inbox/outbox bridge ([director/README.md](director/README.md))
- `shared/schema/` — the plan + result contract; `shared/ENGINE_CONTRACT.md` — what the Blender side implements
- `blender_engine/` — Blender executor (in progress)

Quick start: `python -m pytest director/tests -q`, then see director/README.md.
