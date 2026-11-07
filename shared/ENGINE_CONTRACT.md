# Blender engine contract (what the Blender side must do)

The reference implementation of these semantics is `director/engine_sim.py` + `director/dev/fake_worker.py`.
The Blender worker should behave identically, just with real objects.

## Loop (e.g. `bpy.app.timers.register(poll, persistent=True)`, every ~0.25 s)

1. List `shared/inbox/*.json` (ignore `*.tmp` and dotfiles). Process in name order.
   A file `{"kind": "inspect", "request_id": "..."}` is a read-only inspection: answer with the live scene
   (`kind: "inspect"`, `applied: 0`), apply nothing, delete the inbox file. The director inspects before every plan.
2. Parse the plan. Re-validate EVERY op against the current scene first (defense in depth). If any op is
   invalid, apply nothing (`status: "error"`, `applied: 0`). If Blender raises mid-plan, roll back.
3. Apply operations in order:
   - `add`: create the asset at `position`, set `color` (material base color), `rotation` (degrees, XYZ Euler), `scale` (uniform). Store the director id as a custom property: `obj["director_id"] = op["id"]`.
   - `modify`: find the object by `director_id`; change only the keys in `set`.
   - `delete`: remove the object with that `director_id`.
   - `modify` on an animated channel shifts the whole F-curve (position/rotation: offset, scale: ratio); keys are never wiped.
   - `animate`: key the value the channel ACTUALLY has at `start_frame` (evaluate its F-curve if animated), and `to` at
     `end_frame` (`location` / `rotation_euler` in radians / `scale`); remove keys strictly between. Segments chain.
   - `bounce`: on `location[2]` only, clear keys in [start, end] and key base, base+height, base, ... for `cycles`
     bounces (`frames_per_cycle` apart, peak at the half), ending at base. Ground keys QUAD ease-out, peak keys
     QUAD ease-in. Defaults: height 1, start_frame 1, frames_per_cycle 24.
   - After any animate/bounce/delete, fit `scene.frame_start/frame_end` to the director's keyframes so playback loops
     over the real motion.
4. Write `shared/outbox/<request_id>.json` via a temp file + `os.replace`, matching `shared/schema/scene_result_v1.json`:

```json
{"schema_version": 1, "request_id": "...", "status": "ok|partial|error", "applied": 2,
 "errors": [], "scene_version": 2,
 "scene_objects": [{"id": "mug_1", "asset": "mug", "color": "#3B6FD9", "position": [1,0,0.8], "rotation": [0,0,0], "scale": 1}]}
```

   `scene_objects` must be read back from Blender after execution, not echoed from the plan.
   `scene_version` increments by one when anything was applied.
   Optional read-back (all in `scene_result_v1.json`): `op_results` (one row per op, `ok` + `error`), `scene`
   (`frame_start`, `frame_end`, `frame_current`, `fps`), `other_objects` (non-director objects, read-only), and per
   object `name`, `type`, `parent`, `material`, `animation` (`[{property, axis, keys: [[frame, value], ...]}]`,
   rotation in degrees).
5. Delete the inbox file.

Never execute code from a plan. Unknown ops are errors.
