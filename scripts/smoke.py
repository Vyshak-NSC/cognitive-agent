"""Send one real message to every provider that has its variables set.

Run:  uv run python scripts/smoke.py [provider_id ...]
"""

from __future__ import annotations

import sys

from cpa.providers import Message, ProviderError
from cpa.service import ChatRequest, get_service


def main(only: list[str]) -> int:
    service = get_service()
    failed = 0
    for info in service.providers():
        if only and info.id not in only:
            continue
        if not info.configured:
            print(f"-  {info.id:16} skipped, set {', '.join(info.missing)}")
            continue
        try:
            result = service.chat(
                ChatRequest(messages=[Message("user", "Reply with exactly: ok")], provider=info.id)
            )
        except ProviderError as exc:
            failed += 1
            print(f"FAIL {info.id:16} {exc}")
        else:
            tokens = result.usage.total_tokens if result.usage else "?"
            reply = (result.text or "").strip()[:40]
            print(f"OK   {info.id:16} {result.model} -> {reply!r} ({tokens} tokens)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
