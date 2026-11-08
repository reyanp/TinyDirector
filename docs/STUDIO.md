# Poyo studio (scene-first, voice-first)

Blender's viewport is the product's stage. Poyo is a small companion window beside it: the mascot, a
push-to-talk microphone, short subtitles, the lesson's calculated values, and a status pill. The knowledge map,
the conversation and the agent activity are in a details drawer, hidden by default.

```bash
python -m director.studio --blender        # Blender (stage mode) + Poyo at http://127.0.0.1:8765
python -m director.studio                  # Poyo only, for a worker you started yourself:
blender --python blender_engine/worker.py -- --stage      #   full-window stage viewport (add --rendered on a strong GPU)
python -m director.studio --mock --simulate                 # offline: MOCK model + MOCK voice + SIMULATED engine
python -m director.dev.voice_e2e [--mock]   # scripted proof of the voice demo (synthesized speech, headless Blender)
```

`python -m director.server` still works and serves the same page. Its flags are `--mock`, `--simulate` and `--no-voice`.

## Why a companion window and not a panel inside Blender

| | A: browser window beside Blender (chosen) | B: panel inside Blender |
|---|---|---|
| Microphone | browser `MediaRecorder` (localhost is a secure context) | Blender's Python has no audio capture; it would need new native dependencies inside Blender |
| Poyo's art and animations | the approved `poyo.svg` and its CSS moods, unchanged | would have to be redrawn as GPU or Blender UI drawing |
| Risk to the stage | none; Blender only receives bridge requests | UI code running inside the user's Blender session |
| Cost | Wayland doesn't let an app place windows or keep them on top, so the user tiles the two windows | none |

Option A reuses everything that already works: the bridge, the server, the event stream and the art.
Option B would mean a new UI toolkit and native audio inside Blender. The only cost of A is window
placement on Wayland, which the desktop's tiling handles.

## One voice turn

1. The mic records with `MediaRecorder`. Click to talk; recording stops after about 1.2 s of silence. Or hold
   Space and release to stop. Talking while Poyo speaks stops Poyo (barge-in).
2. `POST /api/voice` sends the raw audio to OpenAI speech-to-text (`gpt-4o-mini-transcribe`, with a circuit
   vocabulary prompt), strips the wake word ("Poyo, …"), then starts the same tutor turn as typed text.
   A `listen` event shows what was heard.
3. Poyo says a short acknowledgment ("Okay, let me show you!") while the turn runs.
4. The turn is the existing pipeline:
   - Nemotron picks ONE tool, given the knowledge-base hint and the scene read back from Blender (lesson
     values and camera focus).
   - `model.py` calculates.
   - Blender applies the change, and its read-back is verified.
   - Nemotron writes the explanation. Numbers are checked against the calculation, and stated misconceptions
     are rejected. One retry, then a template.
5. The turn event carries the verified reply and its stage cues (`director/stage.py`). The browser plays the
   reply from `POST /api/tts` (`gpt-4o-mini-tts`; Poyo speaks only tutor replies and fixed phrases, never
   arbitrary text). Each cue starts at its share of the audio. When a cue starts, `POST /api/direct` sends
   that cue's camera focus and subtitle to Blender.

Voice is transport only. No speech model decides or answers anything. The OpenAI Realtime speech-to-speech
API was considered for natural turn-taking and rejected for this phase: it would generate its own replies
and bypass the Nemotron tool choice, the calculations and the number/misconception checks. Without an
OpenAI key the drawer's Type tab still works, and the browser's own `speechSynthesis` reads the replies.

## Scene-aware requests (no unnecessary regeneration)

| Request | Tool | Blender |
|---|---|---|
| "Teach me why LEDs need resistors." | `teach_led_resistor` | build (once) |
| "What if I make the resistor 1,000 ohms?" | `set_resistance` | update the same objects (values, labels, arrow speed, LED glow) |
| "Increase the resistance." / "make it smaller" | `adjust_resistance` | `step_resistance` picks the next E6 value (330 → 1,000 → 3,300) |
| "Why?" / "Why did it get dimmer?" / "simpler, please" | `explain` | nothing is sent; answered about the circuit on screen |
| "Show me the resistor." / "Zoom out." | `focus_camera` | camera only (`direct_request_v1`); the reply quotes calculated values |
| "Show me what's happening inside this circuit." | `tour_circuit` | camera tour: halo + calculated callout per stop, one narrated sentence each ([WHATIF.md](WHATIF.md)) |
| "Compare what happens if I use 1,000 ohms." | `compare_resistance` | a what-if copy beside the unchanged original (`compare_request_v1`) |
| "Go back to the original." | `end_comparison` | the copy is removed; the original is checked to be exactly as before |
| a new topic (BJT vs MOSFET, voltage divider) | `teach_topic` | the multi-agent lesson pipeline |
| parallel circuits | `explain_concept` | nothing; explanation only (no demo yet) |
| off-topic | `clarify` | nothing |

Build and update requests go to Blender right after the calculation. The subtitle shows the calculated change
while Nemotron writes the explanation; the explanation then replaces it.

## Stage direction (`blender_engine/direction.py`, `shared/schema/direct_request_v1.json`, additive)

- **Targets.** `battery`, `resistor`, `led`, `current` (the whole loop: wires and flow arrows) and `overview` (the
  lesson camera's own framing). A generated lesson uses its part ids.
- **Framing.** A binary search places the target's projected bounding box (part plus its measurement label)
  inside the frame. The camera's HUD labels are parented to it, so the title, equation and subtitle stay on screen.
- **Motion.** In the GUI the camera glides there using `bpy.app.timers` (cubic ease, about 0.9 s). No keyframes
  are added, so lesson playback is unaffected. In background mode the move is immediate. A glowing ring
  highlights the battery, resistor or LED.
- **Display only.** No lesson value changes. The worker answers with `applied: 0` and a `direction` read-back.
  A build clears the direction, and a value update keeps the focus.
- **Cues (`director/stage.py`).** Deterministic keyword rules over the verified explanation text, with no model
  call. The text splits into sentences, then into clauses where the subject changes (", so the LED …",
  "… because the resistance …", ", the current …"). The earliest mention picks the target: LED words (dimmer,
  glow, forward voltage), then resistor, current and battery. A build starts on the overview.
- **Stage mode (`--stage`).** The 3D viewport fills the window with no header, toolbars, overlays or gizmos.
  The view goes through the camera, the camera frame is fitted to the viewport, and the area outside it is
  opaque. Material Preview is the default; `--rendered` gives the EEVEE glow. The launcher sets `TD_SNAPSHOT=0`
  because the companion doesn't show snapshots, and a GUI render after every change is slow on integrated GPUs.

## Verification

- **Offline tests.** `python -m pytest director/tests -q`. `test_stage_voice.py` covers:
  - cues and segmentation, and that `direct_request` rejects invalid requests;
  - `step_resistance`;
  - the demo flow: build, update, then explain with no third request sent;
  - `adjust_resistance` and `focus_camera`;
  - the voice, tts and direct endpoints with the MOCK voice: Poyo refuses arbitrary text, and the endpoints
    return 409 while a turn runs and 503 with no key.
- **Real Blender, headless.** `blender -b --factory-startup --python blender_engine/direction_check.py`.
- **The voice demo.** `python -m director.dev.voice_e2e`:
  - the three spoken requests are synthesized with OpenAI text-to-speech in another voice, standing in for a
    microphone;
  - they go through `/api/voice` → OpenAI STT → Nemotron → real headless Blender (private bridge folder);
  - the script plays the browser's part (TTS plus the cue directions) and checks Blender's read-back:
    - build at 330 Ω, I = 21.2 mA;
    - the same 17 objects updated to 1,000 Ω, I = 7.0 mA, arrows 3.03× slower, LED dimmer;
    - "Why did it get dimmer?" leaves values, labels and animation unchanged;
    - the camera followed every cue.
- **Not tested here.** The real browser microphone path (permissions, `MediaRecorder` and the silence
  detection on your hardware) can't be scripted from this environment. Try it in Firefox or Chrome.

## Limits (this phase)

- Interruption is push-to-talk barge-in (talking stops Poyo). There is no always-on listening and no
  mid-sentence turn-taking.
- Cue timing assumes an even speech rate, so a cue can be a fraction of a second early or late.
- Turn latency is mostly the NVIDIA API: tool choice plus explanation took 3–40 s in the live runs. The
  acknowledgment phrase and the early scene update cover part of the wait.
- Window placement is up to the desktop (Wayland).
