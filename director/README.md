# director/ — Nemotron planning lane

Text → Nemotron → validated JSON scene plan → (optionally) Blender, with the real scene manifest steering every edit.

## Setup (once)

macOS / Linux:
```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r director/requirements.txt
cp .env.example .env        # then edit .env and paste your nvapi-... key
```
Windows PowerShell:
```powershell
py -3 -m venv .venv; .\.venv\Scripts\Activate.ps1
python -m pip install -r director/requirements.txt
copy .env.example .env      # then edit .env
```
Get the key at https://build.nvidia.com/nvidia/nemotron-3.5-super-vl-120b-a12b → "Get API Key" (starts with `nvapi-`).
A Brev `bak-...` key is for the `brev` CLI and does **not** work for model calls.

## Commands (run from the repo root)

```bash
python -m pytest director/tests -q                     # offline tests, no key, no Blender
python -m director.nvidia_smoke                         # one real Nemotron call: model, latency, answer

# dry run: plan + validate only
python -m director.cli --mode create --text "Create a red mug next to a tree" --dry-run
python -m director.cli --mode edit --text "Make only the mug blue" \
    --manifest director/tests/fixtures/scene_manifest.json --dry-run --trace

# execute
python -m director.cli --text "..." --simulate          # pure-Python simulator (labeled SIMULATED)
python -m director.cli --text "..." --send --trace      # real Blender via shared/inbox -> shared/outbox
python -m director.cli --send --trace                   # interactive multi-turn session
python -m director.cli --send --reset --text "..."      # forget saved scene state first
```
Add `--mock` to any command to use the offline rule-based model (prints a MOCK banner; never claim it's Nemotron).
`--json` prints the raw plan; `-v` shows debug logs (never the key).

Without `--manifest`, edits use the **last real result** saved at `shared/state/latest_manifest.json`
(`shared/state/simulated_manifest.json` for `--simulate`).

### Test the bridge without Blender
```bash
python -m director.dev.fake_worker --reset     # terminal 1: SIMULATED worker
python -m director.cli --send --text "Create a red mug next to a tree" --trace   # terminal 2
python -m director.cli --send --text "Change only the mug to blue" --trace
```

## Exit codes
0 ok / clarification · 1 model call failed · 2 config (key, manifest path) · 3 plan invalid after 1 retry · 4 Blender timeout · 5 execution error

## How it works
1. **Intent** – request + mode (`auto`/`create`/`edit`).
2. **Read world** – current scene objects from the manifest; the prompt also lists the next free id per asset.
3. **Plan** – Nemotron gets the exact shared JSON Schema, rules, manifest and request; returns one JSON object.
4. **Validate** – JSON Schema + scene rules (ids exist, no duplicates, no delete-and-recreate, id prefix = asset, frame ranges). One corrective retry with the exact errors, then fail. Model output is never executed.
5. **Execute** – atomic write to `shared/inbox/<request_id>.json`; wait for `shared/outbox/<request_id>.json`.
6. **Observe** – diff before/after: added / changed / removed / unchanged ids; save as the next manifest.
Each run's trace is saved to `shared/state/traces/<request_id>.json`.

## Config (env vars or .env)
| var | default |
|---|---|
| `NVIDIA_API_KEY` | required for live calls |
| `NEMOTRON_MODEL` | `nvidia/nemotron-3.5-super-vl-120b-a12b` |
| `NEMOTRON_BASE_URL` | `https://integrate.api.nvidia.com/v1` (point at a Brev NIM to self-host) |
| `NEMOTRON_THINKING` | `0`; `1` enables reasoning (slower, uses more tokens) |
| `NEMOTRON_TIMEOUT` | `90` seconds |

## Troubleshooting
| symptom | fix |
|---|---|
| `CONFIG ERROR: NVIDIA_API_KEY is not set` | put it in `.env` at the repo root or export it in this terminal |
| `401` / `403` | wrong key or not authorized for that model; regenerate on build.nvidia.com |
| `404` | model id typo; check `NEMOTRON_MODEL` (the non-3.5 `nemotron-3-super` endpoint is deprecated) |
| `429` | rate limited; wait, or set `NEMOTRON_BASE_URL` to your Brev NIM |
| "model returned empty content" | thinking ate the token budget: `NEMOTRON_THINKING=0` |
| `PLAN REJECTED` | read the listed errors; run with `--trace -v` to see the raw output |
| `BRIDGE TIMEOUT`, plan still in inbox | Blender worker isn't running or uses a different `shared/` folder |
