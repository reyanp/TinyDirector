# Blender engine contract (what the Blender side must do)

The reference implementation of these semantics is `director/engine_sim.py` + `director/dev/fake_worker.py`.
The Blender worker should behave identically, just with real objects.

## Loop (e.g. `bpy.app.timers.register(poll, persistent=True)`, every ~0.25 s)

1. List `shared/inbox/*.json` (ignore `*.tmp` and dotfiles). Process in name order.
2. Parse the plan. Re-validate it against the current scene (defense in depth).
3. Apply operations in order:
   - `add`: create the asset at `position`, set `color` (material base color), `rotation` (degrees, XYZ Euler), `scale` (uniform). Store the director id as a custom property: `obj["director_id"] = op["id"]`.
   - `modify`: find the object by `director_id`; change only the keys in `set`.
   - `delete`: remove the object with that `director_id`.
   - `animate`: insert a keyframe of the current value at `start_frame` and of `to` at `end_frame` (`location` / `rotation_euler` in radians / `scale`).
4. Write `shared/outbox/<request_id>.json` via a temp file + `os.replace`, matching `shared/schema/scene_result_v1.json`:

```json
{"schema_version": 1, "request_id": "...", "status": "ok|partial|error", "applied": 2,
 "errors": [], "scene_version": 2,
 "scene_objects": [{"id": "mug_1", "asset": "mug", "color": "#3B6FD9", "position": [1,0,0.8], "rotation": [0,0,0], "scale": 1}]}
```

   `scene_objects` must be read back from Blender after execution, not echoed from the plan.
   `scene_version` increments by one when anything was applied.
5. Delete the inbox file.

Never execute code from a plan. Unknown ops are errors.
