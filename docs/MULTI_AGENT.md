# Multi-agent lessons: teach any (small) electronics question

The circuit tutor no longer stops at "Why does an LED need a resistor?". When the learner asks about a different
idea ("Why would someone use a BJT over a MOSFET?", "How does a voltage divider work?"), the tutor's `teach_topic`
tool hands the question to a team of agents. The team designs a new 3D lesson, builds it, verifies it, and loads
it into Blender.

```
learner question
   │
   ▼
director  (Nemotron)            lesson_spec_v1: parts, pin-to-pin wiring, calculations, labels, steps
   │   validated: schema, ids, real pins, continuous flows, circuit lint, no bare numbers   (1 retry)
   ▼
calculators (Python)            the ONLY source of numbers ({calc.output} placeholders are filled here)
   ▼
builder   (OpenAI, optional)    Python that may ONLY call the lesson toolkit (layout is its job)
   │   sandbox allowlist checked in the director process first
   ▼
lesson_runner                   SEPARATE headless Blender, timeout: build → readback.json, preview.png, lesson.blend
   ▼
verifier  (code + Nemotron VL)  structural + layout from Blender's read-back; vision check of the render
   │   fails → ONE builder repair turn with the exact errors and measured footprints
   │   fails again → deterministic builder (labeled); layout problems become warnings
   ▼
worker {"kind": "lesson"}       appends the VERIFIED lesson.blend as data into your Blender; read back again
```

## Run it

```bash
# add your key to the repo-root .env (gitignored):  OPENAI_API_KEY=sk-...   (OPENAI_BUILDER_MODEL=gpt-5-mini)
python -m director.lesson --text "Why would someone use a BJT over a MOSFET?"          # build + verify only
python -m director.lesson --text "How does a voltage divider work?" --send             # + load into Blender
python -m director.lesson --text "..." --builder deterministic                         # no builder LLM
python -m director.lesson --text "..." --mock --builder deterministic                  # offline, MOCK director
python -m director.tutor          # interactive; ask about a new topic at any time (teach_topic)
```

Each lesson is cached in `shared/state/lesson_cache/<question-key>/`. The folder holds `spec.json`,
`resolved_spec.json`, `code_agent1.py` / `code_agent2.py` / `code_fallback.py`, a `build_*` folder per attempt with
its `.report.json`, and the accepted `lesson.blend`, `preview.png` and `lesson.json`. `--no-cache` rebuilds.

Without `OPENAI_API_KEY`, the deterministic builder writes the scene code and the output says so. Nemotron
still directs, and the vision check still runs.

## What makes the visuals accurate

- **Numbers.** Only `director/lesson/calculators.py` produces numbers: `ohms_law`, `voltage_divider`,
  `led_resistor` and `transistor_switch`. A digit typed into the spec's display text is rejected. Placeholder
  values already include their unit, so "9 V V" is rejected as well.
- **The drawn circuit matches the calculation.**
  - Each calculator declares the parts it assumes (`shown_on`). For example, `transistor_switch` needs a resistor
    labeled `{c.R_load}`, a battery labeled `{c.V_supply}`, and an `{c.R_B}` base resistor when there is a BJT.
  - Every LED needs a series resistor.
  - Batteries can't be shorted.
  - Flows can't run through a capacitor, because the markers show steady DC current.
  - In the first live test, Nemotron drew LEDs with no load resistor while the calculation assumed 330 Ω. The
    lint now forces its retry to draw a real circuit.
- **Wiring is checked geometrically.** Parts have named pins (`blender_engine/lesson_parts.py`). The verifier reads
  each wire's end points back from Blender and requires them to be within 2 cm of the right pins.
- **Flows.** Each flow follows the spec's wires in conventional-current direction. The verifier checks its current
  against the calculation and checks that marker speed is proportional (0.0075 m/frame per mA).
- **Text.** Labels, title, key points and step captions must be the resolved spec text. Any extra label may not
  introduce a number.
- **Layout.**
  - Parts must not overlap, and wires must not pass over other parts.
  - Everything must be inside the frame.
  - Labels must not overlap each other, the parts or the HUD.
- **Readable from the camera.** No part may hide another one from the camera, and no label may sit inside a
  part. Errors give coordinates ("w3 through r1: wire at x -0.62, r1 occupies x -0.98..0.98"), so a repair
  can move things precisely.
- **Typed values.** If the director types a value that exactly equals a calculator output ("5 V" when
  `V_drive` is "5 V"), it is replaced by that placeholder and logged. Any other typed number is still an
  error.
- **Choosing the scene.** If the builder's repaired scene is structurally correct but still has layout issues,
  it is kept when it has no more of them than the deterministic layout; otherwise the fallback wins.
- **Vision.** Nemotron VL looks at the render. A confident mismatch counts as an error (repair); otherwise its
  findings are warnings.
- **After loading.** Once the verified lesson is in your Blender, the director re-runs the structural checks on
  the worker's read-back.

## Safety model for generated code

Builder code is data until it passes the allowlist (`blender_engine/sandbox.py`):

- No imports and no attribute access (`a.b` is rejected outright, which closes the `().__class__` escapes).
- No `while`, `try`, `with`, `lambda`, `class`, `global`, `async` or `yield`.
- Calls must use plain names from the toolkit, safe builtins, math, or functions the code defines itself.
- Names may not start with `_`, `range()` is capped, and code size is capped.

The code then runs in a separate `blender -b --factory-startup` process with a timeout, never in your open
Blender. Your Blender only appends data (meshes, curves, text, camera, lights, animation) from a `lesson.blend`
inside a lesson cache folder; nothing generated is executed there. This is defense in depth, not a perfect Python
sandbox: the process boundary and the no-attribute rule are the main protections.

## Known limits

- Circuits are DC steady state. Flows show constant current, so a capacitor lesson shows the charging current V/R
  (its peak) and doesn't animate the decay. Adding a calculator is the way to extend topics: a function plus a
  `CALCULATORS` entry with `doc`, `args`, `outputs` and `shown_on`.
- The deterministic layout is plain:
  - one circuit becomes a two-row loop;
  - grouped comparisons become side-by-side columns;
  - it detours wires through lanes when the default route would cross a part.
  Its leftover label/wire overlaps are reported as warnings. A better layout is the builder agent's job.
- Builder model: `gpt-5-mini` (default) passes simple lessons (LED, voltage divider, transistor switch) on the
  first try. On the dense 10-part BJT vs MOSFET comparison it usually still leaves a wire or label conflict,
  so the verified deterministic layout is used. A stronger model (`OPENAI_BUILDER_MODEL=gpt-5`) is the knob
  for that. Keep `OPENAI_REASONING_EFFORT` at medium (or unset); `low` hurts the coordinate arithmetic.

## Files

`director/lesson/`:
- `director_agent.py`, `builder.py`, `pipeline.py`, `verify.py`, `spec.py`, `calculators.py`
- `fallback.py`, `agents.py`, `examples.py`, `mock.py`, `__main__.py`

`blender_engine/`: `toolkit.py`, `sandbox.py`, `lesson_runner.py`, `lesson_loader.py`, `lesson_parts.py`

`shared/schema/lesson_spec_v1.json` is additive: no existing schema changed.

Tests: `director/tests/test_lesson.py` (offline, with a fake Blender runner).
