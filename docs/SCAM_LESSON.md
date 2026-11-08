# Spot the Scam: Poyo's one cybersecurity lesson

T-Mobile's scam / fraud challenge, added to Poyo as ONE lesson (no separate app, no new AI model, same frontend).
Say "Poyo, teach me how to recognize scam texts." A 3D phone in Blender shows a **fictional** suspicious text.
You look for the warning signs together, decide what you would do, see both outcomes side by side, and get
practical guidance.

```bash
python -m director.studio --blender          # the normal Poyo studio: just ask about scam texts
python -m director.dev.scam_e2e [--mock]     # end to end: Nemotron (or MOCK) + headless Blender, private bridge
blender -b --factory-startup --python blender_engine/scam_check.py [-- --render DIR --save FILE]
python -m director.scam.evaluate [--json]    # the heuristic detector on its tiny hand-written test set
```

## The lesson

| Learner says | Nemotron's tool | Blender (`scam_request_v1`) | Poyo explains |
|---|---|---|---|
| "Poyo, teach me how to recognize scam texts." | `teach_scam_texts` | `build`: phone + the fictional text, FICTIONAL banner | a short intro, then asks: *"Would you follow this link, or verify the message through your carrier's official app?"* |
| "What makes that message suspicious?" | `scam_inspect` | `mark`: amber tags beside the sender, company name, alert, deadline, link | one sentence per part (the camera walks the message), then the heuristic's limits |
| "What if I follow the link?" / "I'd click it" | `scam_choose {"choice": "follow_link"}` | `outcomes`: A (look-alike page, labeled SIMULATED) and B (official app) beside the untouched message; A marked YOUR CHOICE | what the fake page does with what you type (account takeover, SIM swap), then the safer choice |
| "What should I do instead?" / "I'd check the app" | `scam_choose {"choice": "verify_official"}` | B marked YOUR CHOICE | verify in the official app yourself; forward the text to 7726 (SPAM), then delete it |
| "What if I already typed my password?" | `scam_recover` | nothing (explanation only) | change the password + PIN, call the carrier to lock number transfers, 2-step verification, report it |
| "Go back to the message." | `end_comparison` | `clear_outcomes` | fixed reply |
| any other question while the phone is on stage | `explain` | nothing | answered from the lesson's facts |
| "Teach me why LEDs need resistors." | `teach_led_resistor` | the circuit build clears the phone | the electronics lesson, unchanged |

Nemotron chooses every tool from the learner's own words, with context across turns (Blender's read-back says
what is on stage: phone, tags, which outcome was chosen). It writes every explanation. The checks:

- **Numbers.** Every number in Poyo's text must appear in the turn's FACTS (the fictional message, the detector's
  output, the guidance). The same unit-aware check as the circuit tutor.
- **Knowledge base.** Text that states a known misconception is rejected, e.g. "https means it's safe" or
  "replying STOP is safe".
- **Lesson rules** (`director/scam/lesson.py: lesson_rules`). Poyo's text is rejected if it:
  - advises tapping the link or says replying is safe;
  - claims certainty ("definitely a scam");
  - calls the heuristic "AI" or a "trained model";
  - repeats itself.
- **Shape.** The warning-signs answer must cover the sender, the deadline, the link and the heuristic's limits.

A rejected text gets one retry. After that Poyo uses a deterministic template, labeled as such. The template
sentences are never shown to the model, so a passing explanation is in Nemotron's own words.

Every Blender change is checked against Blender's read-back (`director/scam/check.py`):

- the message is unchanged, word for word;
- the tags match exactly the detector's signs;
- only the chosen outcome is marked;
- both outcomes are labeled hypothetical simulations;
- A is labeled SIMULATED with a `.example` address, and B shows the official app and 7726;
- the camera framing is correct;
- the phone was not rebuilt.

## What is real, what is not

- **Fictional scenario** (`director/scam/scenario.py`). The sender is in the 555-01xx range reserved for fiction.
  The link uses the reserved `.example` domain (RFC 2606), so it can never resolve. "CarrierAlert" is not a company.
  Nothing is opened, sent or collected. Both outcomes are 3D text, labeled hypothetical simulations; no phishing
  page exists anywhere.
- **No T-Mobile data.** The challenge's synthetic dataset, taxonomy and baseline classifier are NOT in this
  repository. The detector is a labeled **HEURISTIC PROTOTYPE** (`director/scam/detector.py`): keyword and
  link rules behind one function, `detect(text, sender_known, segments) -> Detection`. A real baseline could
  replace it behind that function without touching the lesson.
  - It gives a coarse category, the matched warning signs with evidence, a fixed uncertainty statement, and
    protective actions.
  - It never gives a probability, confidence or accuracy.
- **Measured only on a toy set.** `director/scam/eval_set.py` has 30 hand-written fictional messages (15 scam,
  15 legitimate), written by the same authors as the rules.

  | Metric ("likely scam" = positive) | Result |
  |---|---|
  | TP / FP / FN / TN | 12 / 0 / 3 / 15 |
  | Precision | 1.00 |
  | Recall | 0.80 |
  | Accuracy | 0.90 |
  | Any warning sign | 14/15 scams, 2/15 legitimate |

  Misses:
  - a reply-with-your-PIN scam with no link (rated "suspicious");
  - a disconnection threat with no link (rated "suspicious");
  - the "Mom, new number" scam (no signs at all).

  These numbers are optimistic and say nothing about real-world accuracy. The rules were not tuned after
  measuring.
- **Knowledge base.** Seven concepts were added to the existing SQLite KB (`seed.json`), with relations:
  - smishing
  - social engineering
  - impersonation
  - credential theft
  - suspicious links
  - account verification
  - protective actions

  It also gained:
  - 18 facts: 17 cited to FTC, CISA and FCC consumer guidance, plus one `assumption` stating the heuristic's limits;
  - 5 misconceptions with claim regexes;
  - the `spot_the_scam` template, driven by the check `scam.detect` (kind `detector`, never `calculator`);
  - `verify.scam_readback`.

  Facts are *sourced*, not verified (retrieval is not verification).

## Files

| File | Role |
|---|---|
| `director/scam/scenario.py` | the fictional text, the two outcomes, the request builders |
| `director/scam/detector.py` | HEURISTIC PROTOTYPE detector + protective actions |
| `director/scam/eval_set.py`, `evaluate.py` | the 30-message toy evaluation |
| `director/scam/lesson.py` | the turns (teach / inspect / choose / recover / explain / back) and the lesson rules |
| `director/scam/check.py` | read-back checks |
| `director/circuit/agent.py` | the 4 new tools in the shared tool selector; `explain(prompt=, template=)` |
| `shared/schema/scam_request_v1.json` | additive request schema (`build` / `mark` / `outcomes` / `clear_outcomes`) |
| `blender_engine/scam.py` | phone, tags, outlines, the two outcome phones, HUD; hooks for `direction.py` |
| `blender_engine/scam_check.py` | Blender-side self-test |
| `director/dev/scam_e2e.py` | live end-to-end acceptance run |
| `docs/scam/` | renders, the live run record, `.blend` with both outcomes |

Stage direction reuses `direct_request_v1`:
- focus targets are `phone`, `message`, `sender`, `brand`, `body`, `urgency`, `link`, `outcomes`,
  `outcome_a` and `outcome_b`;
- the focused part gets an amber outline instead of the halo ring;
- cues come from deterministic keywords in each sentence.

The browser plays the cues in sync with Poyo's voice, exactly like the circuit lesson.

## T-Mobile challenge requirements

| Requirement | Status |
|---|---|
| Teach recognizing scam / fraud messages, interactively | **Implemented**: one guided lesson, voice or text, 3D phone in Blender |
| Identify warning signs / indicators | **Implemented** with a heuristic prototype (7 signs on the lesson message, with evidence), clearly labeled |
| Show a category, uncertainty and protective actions | **Implemented**: coarse category (no probability), fixed uncertainty statement, actions before / after disclosure |
| Safe verification guidance (official channel, report to 7726) | **Implemented** |
| What to do if information was already disclosed | **Implemented** (`scam_recover`) |
| Consequences of a choice | **Implemented** as two labeled hypothetical simulations side by side |
| Use T-Mobile's synthetic dataset | **Unsupported**: not available here; the lesson uses one fictional message |
| Use T-Mobile's scam taxonomy | **Unsupported**: our 7 indicator ids are our own |
| Compare against / use the baseline classifier | **Unsupported**: not available; the heuristic sits behind an adapter function so it could be swapped in |
| Calibrated confidence, real accuracy | **Not claimed**: only the 30-message toy evaluation above |
| More scenarios (SIM swap lesson, voice scams, ...) | **Out of scope by design** (one lesson only) |

## Verified (2026-11-08)

- **Offline tests.** `python -m pytest director/tests -q`: 266 passed, 15 of them new in `test_scam.py`.
- **Blender self-tests.** `scam_check`, `direction_check`, `compare_check` and `circuit_check`: ALL PASSED.
- **Live end-to-end runs** (Nemotron + headless Blender): `scam_e2e`, `whatif_e2e` and `voice_e2e`, ALL PASSED.
  In `scam_e2e`, Nemotron picked the expected tool on all 6 turns, and every explanation passed the checks on
  Nemotron's own text (no template fallback). The record is in `docs/scam/live_run.json`, with renders in
  `docs/scam/*.jpg`.
- **Not yet verified:** the full lesson by real microphone in the studio.
