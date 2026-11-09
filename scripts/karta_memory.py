"""Look at, or forget, what harness v2 remembers on this Mac (karta/harness/memory.py) and the pinned tasks
(karta/harness/recipes.py, pins.json beside it). Read-only unless forget, unpin or clear.

    uv run python scripts/karta_memory.py show [--kind place|alias|know-how|element|pitfall|profile] [--app example.com]
    uv run python scripts/karta_memory.py forget alias "hemansu" [--app example.com] [--value "Hemanshu Patel"]
    uv run python scripts/karta_memory.py forget profile email --value "jane@example.com"
    uv run python scripts/karta_memory.py clear
    uv run python scripts/karta_memory.py pins
    uv run python scripts/karta_memory.py unpin r3          (an id, or a routine's name)

Only confirmed facts are on disk (what worked in a task proven done); failures live in the running process only.
Know-how, elements, pitfalls and aliases belong to a site ("example.com") or a native app ("Notes"); places and the
user's profile (their own e-mail addresses and names from tasks proven done, the browser in use) to no app.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from karta.harness.memory import DEFAULT_PATH, KINDS, HarnessMemory
from karta.harness.recipes import FILE, RecipeBook


def _day(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d") if t else "never"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", default=str(DEFAULT_PATH), help=f"the memory file (default: {DEFAULT_PATH})")
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show", help="list the facts kept (read-only)")
    show.add_argument("--kind", choices=KINDS)
    show.add_argument("--app", help="a site (example.com) or a native app")
    forget = sub.add_parser("forget", help="delete one fact (every value of the key unless --value)")
    forget.add_argument("kind", choices=KINDS)
    forget.add_argument("key", help="the intent, name said, or know-how text")
    forget.add_argument("--app", default="", help="the site or app it belongs to ('' for places, the profile, app-less aliases)")
    forget.add_argument("--value")
    sub.add_parser("clear", help="delete every fact (pins are kept: unpin them one by one)")
    sub.add_parser("pins", help="list the pinned tasks: templates only, never their values (read-only)")
    unpin = sub.add_parser("unpin", help="remove one pinned task")
    unpin.add_argument("which", help="its id (r3) or its routine's name")
    args = parser.parse_args(argv)

    path = Path(args.path).expanduser()
    pins = path.with_name(FILE)
    if args.command in ("pins", "unpin"):
        if not pins.exists():
            print(f"No pinned tasks at {pins}")
            return 0
        book = RecipeBook(pins)
        if args.command == "pins":
            for r in book.recipes:
                name = f" [{r.name}]" if r.name else ""
                benched = f", failed {r.fails}x in a row" if r.fails else ""
                print(
                    f"{r.id}{name} {r.template!r}  (v{r.version}, {len(r.steps)} steps, used {r.uses}x, last {_day(r.used)}{benched})"
                )
            print(f"{len(book.recipes)} pinned task(s) in {pins}")
        else:
            gone = book.forget(args.which)
            print(f"Unpinned {gone.id} {gone.template!r}" if gone else f"No pinned task {args.which!r}")
        return 0
    if not path.exists():
        print(f"No memory file at {path}")
        return 0
    if args.command == "show":
        facts = HarnessMemory(path, readonly=True).list(args.kind, args.app)
        for f in facts:
            where = f"{f.app}: " if f.app else ""
            value = f" -> {f.value}" if f.value else ""
            how = f"  [{f.how}]" if f.how else ""
            print(
                f"{f.kind:9} {where}{f.key}{value}{how}  (worked {f.good}x, last {_day(f.updated)}{', stale' if f.stale else ''})"
            )
        print(f"{len(facts)} fact(s) in {path}")
    elif args.command == "forget":
        n = HarnessMemory(path).forget(args.kind, args.app, args.key, args.value)
        print(f"Forgot {n} fact(s)")
    else:
        n = HarnessMemory(path).clear()
        print(f"Cleared {n} fact(s) from {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
