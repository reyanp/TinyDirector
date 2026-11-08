"""Poyo studio launcher: Blender is the stage, Poyo is a small companion window beside it.

  python -m director.studio                 # Poyo server (Nemotron + OpenAI voice) for a Blender worker you started
  python -m director.studio --blender       # also start Blender: full-window stage viewport, worker on the bridge
  python -m director.studio --mock --simulate   # offline: MOCK model + MOCK voice + SIMULATED engine (labeled)

Then open http://127.0.0.1:8765 (Firefox/Chrome: allow the microphone) and place the two windows side by side:
Blender large (the stage), the Poyo window narrow on the right. On Wayland the window manager owns placement, so
tile them with your desktop's tiling or drag them. Talk with the mic button or by holding Space.

--blender starts `blender --python blender_engine/worker.py -- --stage` with TD_SNAPSHOT=0 (the companion window
does not show snapshots, and a GUI render after every change is slow on integrated GPUs). Add --rendered for the
EEVEE glow viewport on a strong GPU. Don't start a second worker if one is already running on shared/.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

from . import paths
from .nemotron_client import ConfigError
from .server import make_session, serve


def start_blender(rendered: bool) -> subprocess.Popen:
    exe = os.environ.get("BLENDER") or shutil.which("blender")
    if not exe:
        raise ConfigError("Blender not found: put it on PATH or set BLENDER=/path/to/blender")
    cmd = [exe, "--python", str(paths.REPO_ROOT / "blender_engine" / "worker.py"), "--", "--stage"]
    if rendered:
        cmd.append("--rendered")
    env = {**os.environ, "TD_SNAPSHOT": os.environ.get("TD_SNAPSHOT", "0")}
    print("[studio] starting Blender:", " ".join(cmd[1:]), flush=True)
    return subprocess.Popen(cmd, cwd=paths.REPO_ROOT, env=env)


def main(argv: list[str] | None = None) -> int:
    paths.load_env()
    ap = argparse.ArgumentParser(prog="python -m director.studio", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--blender", action="store_true", help="also start the Blender GUI worker in stage mode")
    ap.add_argument("--rendered", action="store_true", help="with --blender: EEVEE rendered viewport")
    ap.add_argument("--mock", action="store_true", help="offline MOCK model + MOCK voice (NOT Nemotron/OpenAI)")
    ap.add_argument("--simulate", action="store_true", help="SIMULATED circuit engine instead of Blender")
    ap.add_argument("--no-voice", action="store_true", help="text only (the Type tab)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for Blender per request")
    args = ap.parse_args(argv)
    try:
        session = make_session(args.mock, args.simulate, args.timeout, voice=not args.no_voice)
        blender = start_blender(args.rendered) if args.blender and not args.simulate else None
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    httpd = serve(session, "127.0.0.1", args.port)
    v = session.mode["voice"]
    voice_line = (f"{v['provider']} {v.get('stt', '')} / {v.get('tts', '')}" if v.get("provider")
                  else f"off ({v['error']}) - use the Type tab")
    print(f"Poyo: http://127.0.0.1:{httpd.server_address[1]}\n  model:  {session.mode['model']}\n"
          f"  engine: {session.mode['executor']}\n  voice:  {voice_line}", flush=True)
    print("Put Blender (the stage) and the Poyo window side by side; click the mic or hold Space to talk.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        if blender is not None and blender.poll() is None:
            blender.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
