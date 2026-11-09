"""Laya's recorded questions (`karta --record`) as JSONL, one line per question, for hand-labelling and later for
fine-tuning or comparing models.

    uv run python scripts/laya_dataset.py                          # every recording in Application Support/karta/snapshots
    uv run python scripts/laya_dataset.py DIR_OR_FILE ... -o laya.jsonl

Each line: the run's file, the task, the question (its instructions and its placeholders' values), the options, the
screen it was asked against as text (one item per line, as Laya reads items), Laya's choice and its probabilities,
and an empty `label` to fill in by hand (the option that was right). A recording from before the screen and values
were kept gives them empty. Local only: the output holds screen content, so it is never committed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from karta.harness.snapshots import SNAPSHOTS, screen_back


def rows(path: Path):
    record = json.loads(path.read_text())
    screens = record.get("screens") or []
    for n, q in enumerate(record.get("laya") or []):
        options, probs = list(q.get("options") or []), list(q.get("probs") or [])
        read = q.get("read")
        screen = screens[read] if isinstance(read, int) and 0 <= read < len(screens) else None
        yield {
            "run": path.name,
            "n": n,
            "task": record.get("task", ""),
            "question": q.get("instructions", ""),
            "values": q.get("values") or {},
            "options": options,
            "screen": "\n".join(i.describe() for i in screen_back(screen).items) if screen else "",
            "chosen": options[max(range(len(probs)), key=probs.__getitem__)] if options and len(probs) == len(options) else None,
            "probs": dict(zip(options, probs, strict=False)),
            "label": "",
        }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", type=Path, default=[SNAPSHOTS], help="recordings or folders of them")
    ap.add_argument("-o", "--out", type=Path, help="the JSONL file (default: standard output)")
    args = ap.parse_args(argv)
    files = sorted(f for p in args.paths for f in (p.expanduser().glob("*.json") if p.is_dir() else [p.expanduser()]))
    out = args.out.open("w") if args.out else sys.stdout
    count = 0
    try:
        for f in files:
            for row in rows(f):
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                count += 1
    finally:
        if args.out:
            out.close()
    print(f"{count} question(s) from {len(files)} recording(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
