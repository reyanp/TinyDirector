"""Download every third-party file listed in assets/catalog.json into assets/cache/.

  python assets/fetch.py            # fetch what's missing (idempotent)
  python assets/fetch.py --check    # verify cached files against Poly Haven's MD5s, download nothing

Standard library only. Only Poly Haven (CC0) is supported; each file's MD5 is checked against the
value Poly Haven's API publishes, and a per-asset manifest.json records URL, MD5, license and author.
Nothing downloaded is ever executed: textures/HDRIs are images, models are glTF (JSON + binary buffers).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
API = "https://api.polyhaven.com"
UA = {"User-Agent": "TinyDirector-hackathon/0.1 (asset fetch)"}
ALLOWED_HOSTS = ("https://dl.polyhaven.org/",)


def _get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _wanted_files(src: dict, files: dict) -> list[tuple[str, str, str]]:
    """(relative path, url, md5) for every file this catalog entry needs."""
    res, out = src["resolution"], []
    if src["kind"] == "hdri":
        f = files["hdri"][res][src["format"]]
        out.append((Path(f["url"]).name, f["url"], f["md5"]))
    elif src["kind"] == "texture":
        for m in src["maps"]:
            f = files[m][res]["jpg"]
            out.append((Path(f["url"]).name, f["url"], f["md5"]))
    elif src["kind"] == "model":
        f = files["gltf"][res]["gltf"]
        out.append((Path(f["url"]).name, f["url"], f["md5"]))
        for rel, inc in f.get("include", {}).items():
            out.append((rel, inc["url"], inc["md5"]))
    for rel, url, _ in out:
        if not url.startswith(ALLOWED_HOSTS) or ".." in Path(rel).parts or Path(rel).is_absolute():
            raise ValueError(f"refusing unexpected file {rel!r} from {url!r}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="verify only, no downloads")
    args = ap.parse_args()
    catalog = json.loads((HERE / "catalog.json").read_text())
    bad = 0
    for src in catalog["sources"]:
        if src["provider"] != "polyhaven" or src["license"] != "CC0-1.0":
            print(f"skip {src['id']}: only CC0 Poly Haven sources are fetched automatically")
            continue
        dest = CACHE / src["id"]
        files = json.loads(_get(f"{API}/files/{src['provider_id']}"))
        wanted = _wanted_files(src, files)
        for rel, url, md5 in wanted:
            path = dest / rel
            if path.exists() and _md5(path) == md5:
                continue
            if args.check:
                print(f"MISSING/BAD {src['id']}/{rel}")
                bad += 1
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            data = _get(url)
            if hashlib.md5(data).hexdigest() != md5:
                print(f"MD5 MISMATCH {src['id']}/{rel} - not saved")
                bad += 1
                continue
            path.write_bytes(data)
            print(f"got {src['id']}/{rel} ({len(data) // 1024} KB)")
        if not args.check:
            (dest / "manifest.json").write_text(json.dumps({
                "id": src["id"], "provider": "polyhaven", "provider_id": src["provider_id"], "page": src["page"],
                "license": src["license"], "author": src["author"],
                "files": [{"path": r, "url": u, "md5": m} for r, u, m in wanted]}, indent=2))
        print(f"ok  {src['id']} ({len(wanted)} files, {src['license']}, {src['author']})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
