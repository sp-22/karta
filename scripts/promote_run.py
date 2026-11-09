"""Promote a recorded run (`karta --record`) to an offline replay test, scrubbed so it is safe to commit.

    uv run python scripts/promote_run.py ~/Library/Application\\ Support/karta/snapshots/<file>.json <name>
    uv run python scripts/promote_run.py <file>.json <name> --expect failed --why "the page showed the old send"

Writes tests/replay/<name>/run.json (the recording, every word of content replaced by a stand-in, a Laya question's
placeholder values as typed text is: see karta/harness/replay.py) and expected.json (the outcome the replay must reach: done or failed honestly, and at most
the recorded brain calls). Then it replays the case once and prints what happened. `--expect` overrides the outcome
when the live run itself was wrong (a false done is promoted with --expect failed: the test fails until it is fixed).
The scrub salt is this Mac's own (~/Library/Application Support/karta/scrub-salt) unless --salt is given, so a
stand-in cannot be traced back to a name from the repo alone. Nothing here touches the screen or the network.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from karta.harness.replay import CASES, leaks, local_salt, outcome_problems, replay, scrub_record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("recording", type=Path, help="a snapshot file written by karta --record")
    ap.add_argument("name", help="the case's folder name under tests/replay/ (letters, digits and dashes)")
    ap.add_argument("--expect", choices=("done", "failed"), help="the outcome the replay must reach (default: the run's)")
    ap.add_argument("--why", default="", help="one line: what this case guards against")
    ap.add_argument("--salt", help="the scrub salt (default: this Mac's own)")
    ap.add_argument("--out", type=Path, default=CASES, help="where cases live (default: tests/replay)")
    ap.add_argument("--force", action="store_true", help="replace a case of that name")
    args = ap.parse_args(argv)

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.name):
        ap.error("name: lowercase letters, digits and dashes")
    record = json.loads(args.recording.expanduser().read_text())
    if int(record.get("version", 1)) < 2 or "events" not in record:
        ap.error("this recording predates replay (version 1): record the run again with karta --record")
    folder = args.out / args.name
    if folder.exists() and not args.force:
        ap.error(f"{folder} exists (use --force to replace it)")

    scrubbed = scrub_record(record, args.salt or local_salt())
    left = leaks(scrubbed)
    if left:  # by construction there are none: refuse rather than commit one
        print(f"refused: {len(left)} word(s) were not scrubbed", file=sys.stderr)
        return 1
    calls = len(scrubbed.get("brain", []))
    expected = {
        "done": (args.expect == "done") if args.expect else bool(record.get("done")),
        "brain_calls": calls,
        "why": args.why,
    }
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "run.json").write_text(json.dumps(scrubbed, indent=1, ensure_ascii=False) + "\n")
    (folder / "expected.json").write_text(json.dumps(expected, indent=1) + "\n")
    print(f"wrote {folder}/run.json and expected.json ({len(scrubbed.get('actions', []))} actions, {calls} brain replies)")

    out = replay(scrubbed)
    problems = outcome_problems(expected, out)
    if problems:
        print("the replay does not reach the expected outcome yet:\n  " + "\n  ".join(p.replace("\n", "\n  ") for p in problems))
        print("(mark it xfail in expected.json with the reason, or fix the harness)")
        return 2
    print(f"replays: done={out.done}, {out.brain_calls} brain call(s), {len(out.ran)} action(s) as recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
