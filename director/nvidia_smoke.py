"""One real Nemotron call to prove access.  Run:  python -m director.nvidia_smoke

Prints model id, latency, answer, reasoning length and token usage. Never prints the key.
"""

from __future__ import annotations

import sys

from .nemotron_client import ConfigError, ModelCallError, NemotronClient
from .paths import load_env


def main() -> int:
    load_env()
    try:
        client = NemotronClient(temperature=0.0)
        print(f"endpoint: {client.base_url}\nmodel:    {client.model}\nthinking: {client.thinking}")
        reply = client.complete([{"role": "user", "content": "Reply with only the word READY."}], max_tokens=512)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    except ModelCallError as exc:
        print(f"CALL FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"answered by: {reply.model}\nlatency:  {reply.latency_s:.2f}s")
    print(f"answer:   {reply.content.strip()!r}")
    print(f"reasoning chars: {len(reply.reasoning)}\nusage:    {reply.usage}")
    if not reply.content.strip():
        print("WARNING: empty content. Turn thinking off (NEMOTRON_THINKING=0) or raise max_tokens.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
