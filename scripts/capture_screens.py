"""Save the front app's screen as a JSON fixture, read-only, to replay real screens to Laya's decisions.

It reads through karta.harness.mac.MacHarnessEnv and never clicks, types, presses or brings any app to
the front: bring the app forward yourself during the countdown. Items are saved with every field but the
env's handle (ref).

    uv run python scripts/capture_screens.py --name chat-list --delay 5
    uv run python scripts/capture_screens.py --name notes --repeat 3 --every 4
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

from karta.harness.screen import screen_to_json

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "screens"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "screen"


def capture_one(env, name: str, out: Path) -> Path:
    """Read the screen once (no action) and write it as `<name>-<time>.json`."""
    env._last = None  # a fresh read, never an action
    started = time.perf_counter()
    screen = env.screen()
    data = {
        **screen_to_json(screen),
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "read_ms": round(1000 * (time.perf_counter() - started)),
        "fingerprint": screen.fingerprint(),
    }
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{slug(name)}-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="", help="fixture name (default: the front app's name)")
    parser.add_argument("--out", type=Path, default=OUT, help=f"folder for the JSON files (default {OUT})")
    parser.add_argument("--delay", type=float, default=3.0, help="seconds to bring the app forward yourself")
    parser.add_argument("--repeat", type=int, default=1, help="how many captures")
    parser.add_argument("--every", type=float, default=3.0, help="seconds between captures")
    args = parser.parse_args()

    for left in range(int(args.delay), 0, -1):
        print(f"capturing in {left} ...", flush=True)
        time.sleep(1)
    from karta.harness.mac import MacHarnessEnv

    env = MacHarnessEnv(app=None)  # no app: read whatever is in front, activate nothing
    for i in range(args.repeat):
        if i:
            time.sleep(args.every)
        path = capture_one(env, args.name or env.app, args.out)
        screen = env.screen()
        print(f"{path}  ({screen.app}, {len(screen.page())} page items, {len(screen.items)} in all)")


if __name__ == "__main__":
    main()
