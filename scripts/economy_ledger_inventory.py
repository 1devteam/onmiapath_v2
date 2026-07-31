#!/usr/bin/env python3
"""Emit a signed, read-only legacy economy migration inventory."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import redis.asyncio as redis

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.economy.migration import inventory_legacy_tenant  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SCAN and verify one tenant's legacy economy state without writing Redis"
    )
    parser.add_argument("--redis-url", required=True, help="Redis URL containing legacy keys")
    parser.add_argument("--tenant", required=True, help="Exact tenant identifier")
    parser.add_argument("--signature-key-id", required=True, help="Non-secret signing key label")
    return parser.parse_args()


async def _run() -> int:
    args = _arguments()
    secret_text = os.environ.get("ECONOMY_MIGRATION_MANIFEST_SECRET")
    if secret_text is None:
        raise SystemExit("ECONOMY_MIGRATION_MANIFEST_SECRET is required")
    client = redis.from_url(args.redis_url, decode_responses=True)
    try:
        manifest = await inventory_legacy_tenant(
            client,
            args.tenant,
            signing_secret=secret_text.encode("utf-8"),
            signature_key_id=args.signature_key_id,
        )
    finally:
        await client.aclose()
    print(json.dumps(asdict(manifest), ensure_ascii=False, sort_keys=True, indent=2))
    return 2 if manifest.quarantine else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
