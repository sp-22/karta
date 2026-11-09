"""Offline re-check of the outcome contract (karta/harness/contract.py) on recorded runs: no Mac, no brain.

Each case names a recording by its stamp, the contract a plan for its task declares, and what to judge: the items a
recorded answer said (each matched to the screen it was read off, by the role the content model gives it there), a
comparison answer, or an outcome on the recorded screens. It prints the recorded ending beside the contract's verdict,
in numbers and kinds only, never screen content (recordings hold the user's own pages).

    uv run python scripts/contract_check.py                  # the default recordings (~/karta-everyday)
    uv run python scripts/contract_check.py --runs DIR
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from karta.harness import content, contract
from karta.harness.snapshots import screen_back
from karta.harness.tools import Collected

RUNS = Path.home() / "karta-everyday"
CASES = [  # stamp, the contract a plan declares for the task, what is judged
    ("20261107-111923", "list(3, titles)", "said"),  # a pinned replay read the page before the results
    ("20261107-111957", "list(2, names)", "said"),
    ("20261107-110842", "list(events)", "said"),  # an event editor's labels were said as events
    ("20261107-111434", "comparison(AirPods Pro vs Galaxy Buds on price)", "answer"),  # ended "Done." with no answer
    ("20261107-110644", "created(reminder named Karta test: call the bank)", "outcome"),  # made, but the task looped
    ("20261107-110812", "changed(Do Not Disturb to on)", "outcome"),  # its pane opened: a false "Done"
    ("20261107-105538", "opened(latest email)", "outcome"),
]


def load(runs: Path, stamp: str) -> dict | None:
    hit = sorted(runs.glob(f"{stamp}-*.json"))
    return json.loads(hit[0].read_text()) if hit else None


def judge_said(record: dict, c: contract.Contract) -> dict:
    """The items the recorded answer said: the screen items whose whole name it holds (longest first, each once,
    from the latest read that shows it), each kept with the role the content model gives it on that read."""
    answer = content.normal(record.get("answer") or "")
    screens = [screen_back(s) for s in record.get("screens", [])]
    kept, taken = [], []
    for scr in reversed(screens):
        roles = content.roles(scr)
        for i in sorted(scr.page(), key=lambda i: -len(i.name)):
            name = content.normal(i.name)
            if len(name) < 4 or name not in answer or any(name in t for t in taken):
                continue
            taken.append(name)
            kept.append(Collected(i.name, i.url or "", role=roles[id(i)]))
    good, why = contract.listed(c, kept)
    final = screens[-1] if screens else None
    return {
        "said": len(kept),
        "roles": sorted(k.role for k in kept),
        "met": not why,
        "kept": len(good),
        "why": why.split(" (")[0],
        "titles on the last read": len(content.title_ids(final)) if final is not None else 0,
    }


def judge_answer(record: dict, c: contract.Contract) -> dict:
    answer = str(record.get("answer") or "")
    if not answer:
        return {"answer said": False, "met": False, "why": "no answer was said: a comparison is an answer, never 'Done.'"}
    return {"answer said": True, "met": None, "why": "re-check needs the collected rows"}


def judge_outcome(record: dict, c: contract.Contract) -> dict:
    screens = [screen_back(s) for s in record.get("screens", [])]
    first = next((n for n, s in enumerate(screens) if s.app != screens[0].app), 0) if screens else 0
    start = screens[first] if screens else None  # the first read in the task's own app
    for n, now in enumerate(screens[first + 1 :], first + 1):
        refs = screens[first:n]
        if c.kind == "created":
            why = contract.appeared(refs, now, c.name) or next((w for r in refs if (w := contract.counted(r, now))), "")
        elif c.kind == "changed":
            why = contract.state(now, c.name, c.value)
        else:
            why = contract.opened(start, now, c.name, [])
        if why:
            return {"met": True, "on read": n, "of reads": len(screens), "why": re.sub(r"'[^']*'", "'…'", why)}
    return {"met": False, "reads": len(screens)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", default=str(RUNS))
    runs = Path(parser.parse_args().runs)
    for stamp, text, what in CASES:
        record = load(runs, stamp)
        if record is None:
            print(f"{stamp}  (no recording)")
            continue
        c = contract.parse(text)[0]
        verdict = {"said": judge_said, "answer": judge_answer, "outcome": judge_outcome}[what](record, c)
        ran = {"recorded done": bool(record.get("done")), "brain calls": record.get("brain_calls")}
        print(f"{stamp}  {c.kind:10s} {json.dumps({**ran, **verdict})}")


if __name__ == "__main__":
    main()
