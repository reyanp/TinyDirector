# TinyDirector — notes for Claude Code

- `shared/schema/*.json` and `shared/fixtures/*.json` are the contract between the director and the Blender engine. Do not change them without the team agreeing; fix code instead.
- `director/` = Nemotron planning lane (Neal). `blender_engine/` = Blender executor (Reyan). Don't edit the other lane.
- Plans are DATA. Never eval/exec model output. Max ONE corrective retry, then fail without executing.
- Never print, log, or commit API keys. Keys live in the gitignored `.env`.
- Tests must pass offline: `python -m pytest director/tests -q`.
- Anything mocked or simulated must be labeled MOCK / SIMULATED in output. Never present it as a live Nemotron call or real Blender.
