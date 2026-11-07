"""Prove Nemotron VL can see an image.   python -m director.vision_smoke [path/to/image.png]

Default image: the newest snapshot in shared/outbox, else docs/sample_scene.jpg.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import paths
from .nemotron_client import ConfigError, ModelCallError, NemotronClient
from .verifier import _data_url


def main() -> int:
    paths.load_env()
    if len(sys.argv) > 1:
        image = Path(sys.argv[1])
    else:
        snaps = sorted(list(paths.OUTBOX.glob("*.jpg")) + list(paths.OUTBOX.glob("*.png")), key=lambda p: p.stat().st_mtime)
        image = snaps[-1] if snaps else paths.REPO_ROOT / "docs" / "sample_scene.jpg"
    if not image.exists():
        print(f"no image at {image}", file=sys.stderr)
        return 2
    try:
        client = NemotronClient(temperature=0.1)
        reply = client.complete([{"role": "user", "content": [
            {"type": "text", "text": "List every distinct object you can see in this 3D render and its color. One line each."},
            {"type": "image_url", "image_url": {"url": _data_url(image)}}]}], max_tokens=600)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    except ModelCallError as exc:
        print(f"CALL FAILED: {exc}\n(If this is a 4xx about the image, the endpoint may not accept base64 data URLs.)",
              file=sys.stderr)
        return 1
    print(f"image: {image}\nmodel: {reply.model}\nlatency: {reply.latency_s:.2f}s\n---\n{reply.content.strip()}")
    return 0 if reply.content.strip() else 1


if __name__ == "__main__":
    sys.exit(main())
