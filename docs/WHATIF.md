# Poyo's Cinematic What-If Lab

This feature is built from the existing pieces: Nemotron picks tools, `model.py` calculates, Blender executes and
reads back, and Poyo speaks. Nothing was added to the frontend apart from passing a callout. No new model or
dependency was added.

```bash
python -m director.studio --blender         # then say or type:
#   "Teach me why LEDs need resistors."
#   "Poyo, show me what's happening inside this circuit."             -> cinematic tour
#   "Compare what happens if I increase the resistor to 1,000 ohms."  -> split reality
#   "Why is the LED on the right dimmer?"                              -> answered about both, nothing changes
#   "Okay, go back to the original circuit."                           -> the copy goes, the original is untouched
python -m director.dev.whatif_e2e [--mock]  # the same 5 turns, scripted, real headless Blender, private bridge
blender -b --factory-startup --python blender_engine/compare_check.py -- [--render DIR] [--save FILE.blend]
```

## 1. Cinematic teaching (`tour_circuit`)

- **Nemotron's part.**
  - It reads the scene description that comes from Blender's read-back.
  - It chooses `tour_circuit` and the stops in teaching order, e.g. `battery → resistor → current → led → overview`.
  - It narrates exactly one sentence per stop.
  - The narration has to pass the number check (only calculated numbers) and a structure check (one sentence per
    stop, so the camera can follow).
  - If a check fails, there is one retry, then the deterministic tour text, labeled `template`.
- **Calculator's part.** `model.py` supplies each stop's big on-screen measurement (`tutor.tour_callouts`):
  - battery: 9 V;
  - resistor: 7.0 V across 330 Ω;
  - current: 21.2 mA, the same all the way around;
  - LED: ≈ 2 V forward voltage (simplified model);
  - overview: I = 21.2 mA with (9 V − 2 V) ÷ 330 Ω.
- **Blender's part** (`blender_engine/direction.py`, `direct_request_v1` with the new `callout` field). Each cue:
  - glides the camera to the part (smooth `bpy.app.timers` tween in the GUI; immediate headless);
  - rings it with a halo;
  - pops in a large top-right callout with the measurement;
  - emphasises the current arrows (×3 emission) while the camera is on the current;
  - returns to the full circuit at the end.

  It uses the existing objects; nothing is rebuilt and no lesson value changes (`applied = 0`).
- **What you see.** Every camera move is a real request with its read-back. It appears in Poyo's Activity drawer as
  `camera  Camera → resistor — read-back: focus resistor, halo on resistor, callout 7.0 V`.
- **Honest visuals.**
  - The arrows are conventional current (+ to −).
  - Their speed is proportional to the calculated current.
  - They are not electron trajectories or drift speed. The HUD legend says so.

## 2. Split-reality comparison (`compare_resistance`, `blender_engine/compare.py`)

- **Request.** `compare_request_v1` (additive schema) with `show` / `clear`.
  - The director sends the baseline: the circuit Blender reports on stage. Blender rejects a stale baseline.
  - It also sends the alternative's calculated display (`display_for(solve(...))`), the same labels, arrow speed,
    glow and resistor bands as a real update.
- **Blender.**
  - **Duplicates** the lesson objects into a separate `TD_Compare` collection, 8.6 m to the right. Shared data where
    identical; own data for the labels, resistor bands, LED material, light and flow.
  - **Applies the what-if values** to the copy.
  - **Adds** the `NOW · R = 330 Ω` and `WHAT IF · R = 1,000 Ω` headlines with each equation, a divider, the HUD title
    "What if the resistor were 1,000 Ω?" and the legend: *"Arrow speed and LED glow are illustrative, scaled with
    the calculated current (not electron speed or measured brightness)."*
  - **Hides** the lesson's own title and equation, which are restored later.
  - **Sets playback** to start at frame 100 so both circuits are powered and animating together.
  - **Frames** both sides.
- **Non-destructive.** The original objects keep the same uid, position, values, labels, arrows and glow (checked).
  `clear` removes every comparison object and material and restores the HUD, the frame range and the camera.
  A value change (`set_resistance`) or a new lesson ends a comparison first.
- **Director checks** (`check.run_compare`, 13 checks) against Blender's read-back:
  - the copy's stored values;
  - its current label against I recomputed from those stored values;
  - all of its labels;
  - its arrow speed (3.03× slower for 1,000 Ω);
  - its LED glow;
  - its bands and headlines;
  - the camera framing;
  - the original is unchanged.

  Only then is the explanation written.

## 3. Conversation (Nemotron picks every tool; nothing is scripted on the model side)

| Learner | Nemotron's tool | What runs |
|---|---|---|
| "Show me what's happening inside this circuit." | `tour_circuit {stops}` | callouts calculated → narration (checked) → cue-by-cue camera |
| "Compare what happens if I increase the resistor to 1,000 ohms." | `compare_resistance {resistance_ohm: 1000}` | both solved → compare show → 13 read-back checks → explanation quoting both currents |
| "Compare it with a bigger one." | `compare_resistance {direction: "increase"}` | `step_resistance` picks the next E6 value (330 → 1,000 Ω) |
| "Why is the LED on the right dimmer?" | `explain` | facts include both circuits (`comparison_on_screen`); cues look at the side each sentence names; nothing sent |
| "Go back to the original circuit." | `end_comparison` | compare clear → the original is checked to be exactly as before → calculated reply |

Comparison cues (`stage.compare_cues`): a sentence that names the what-if values (its resistance or current) looks
at the right-hand copy with its callout; one that names the original values looks at the left; the last one looks
at both.

## Results (2026-11-08)

**Offline tests.** `python -m pytest director/tests -q`: 251 passed (13 new in `test_whatif.py`).

**Real Blender, headless.**
- `compare_check.py`: ALL PASSED.
- `direction_check.py`: ALL PASSED.

**Live What-If e2e.** `whatif_e2e`: Nemotron + real headless Blender, ALL PASSED in 5 consecutive runs (2–6).
- Nemotron chose the expected tool in every turn of every run.
- Run 2 exposed a bug that its checks then missed: Nemotron narrated 3 sentences for 5 stops, so the camera skipped
  two stops. Fixed with the structure check, and the e2e now asserts that every chosen stop is visited.
- Run 4 fell back to the template once, because the retry instruction contradicted the tour. Fixed.
- In runs 5 and 6 the narration was Nemotron's own, after at most one repair.

**GUI stage.** The smooth glide (timer tween), the callout pop-in, the comparison and the return were driven in a real
Blender window on a private folder. Every request was ok, and the read-back reported `moving: true` during the glide.

**Regressions.**
- `voice_e2e` live: ALL PASSED (OpenAI STT/TTS + Nemotron + Blender).
- `director.lesson` BJT vs MOSFET live: built and verified, 38 checks, 1 layout warning of the existing kind.
- The Poyo art (`poyo.svg`) and speech path are unchanged.

## Files

`docs/whatif/` (from the last live run):
- `tour_1_battery.jpg` … `tour_5_overview.jpg`: the tour, as rendered by the worker after each cue.
- `compare.jpg`, `compare_focus_now.jpg`, `compare_focus_whatif.jpg`: the comparison.
- `back_to_original.jpg`: after going back.
- `compare_preview.mp4`: 4 s of the comparison animating, rendered from `whatif_compare.blend`.
- `gui_stage_tour_glide.jpg`, `gui_stage_compare.jpg`: screenshots of the real GUI stage (`worker.py -- --stage`,
  private bridge folder). The first is mid-glide to the resistor, with the callout already shown.
- `whatif_compare.blend`: the comparison on stage.
- `whatif_final.blend`: after going back.
- `live_run.json`: every turn's tool, events, reply, cues, camera read-backs and the circuit read-back.

## Limits

- **Visual nit.** In tight part close-ups, a neighbouring part's measurement label can show at the frame edge.
- **Cue timing.** Cues assume an even speech rate, so a camera move can be a fraction of a second early or late.
- **One parameter.** The comparison varies the resistor only; the battery and the LED are fixed in this lesson.
- **No resistor removal.** "What if I remove the resistor?" is answered as an explanation, with the model's limits:
  nothing limits the current, and a real LED would quickly burn out. There is no visual warning yet (Stage 4 was
  optional and not built). No current is calculated for R = 0.
