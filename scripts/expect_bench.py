"""Offline check of the typed verifier (karta/harness/expect.py) on recorded runs: no Mac, no brain.

Each recorded action has the screen it acted on and the screens read after it (recorder v2). A labels file names
steps by recording and action number, the expectation a v2 plan declares for that step (an open_place's implied
page_on included), and whether the step really worked, judged by hand from the screens. The script runs
expect.verify on each step's before and after screens and reports agreement. It prints numbers and stamps only,
never screen content (recordings hold the user's own pages).

    uv run python scripts/expect_bench.py                 # the default recordings and labels
    uv run python scripts/expect_bench.py --misses        # also each disagreement: stamp, action, expect, verdict

Labels (JSON list):
    {"run": "20261011-172622", "act": 1, "expect": "new_item", "worked": true}
    {"run": "...", "act": 7, "after": 9, "expect": "item_opened(...)", "worked": false, "false_done": "a name"}
    "after": the action whose after-screen ends the span (a done check spans several actions); "typed" and "sent":
    the plan's typing and, on a submit, the text it sends; "tool": the step's tool when not a click.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from karta.harness import expect
from karta.harness.snapshots import screen_back

RUNS = Path.home() / "karta-everyday"
LABELS = Path.home() / "Desktop" / "Projects" / "karta-research" / "designs" / "typed-checks" / "labels.json"


def spans(record: dict) -> list[tuple]:
    """Per recorded action: (the screen it acted on, the last screen read before the next action)."""
    screens = [screen_back(s) for s in record.get("screens", [])]
    events, acts = record.get("events", []), record.get("actions", [])
    at = [n for n, e in enumerate(events) if "act" in e]
    out = []
    for j, p in enumerate(at):
        act = acts[events[p]["act"]]
        reads = [e["read"] for e in events[:p] if "read" in e]
        before = screen_back(act["screen"]) if act.get("screen") else screens[reads[-1]] if reads else None
        end = at[j + 1] if j + 1 < len(at) else len(events)
        later = [e["read"] for e in events[p + 1 : end] if "read" in e]
        after = screens[later[-1]] if later else screen_back(record["final_screen"]) if record.get("final_screen") else before
        out.append((before, after))
    return out


def run(labels: list[dict], runs: Path) -> list[dict]:
    files = {p.name[:15]: p for p in runs.glob("*.json")}
    cache: dict[str, list[tuple]] = {}
    out = []
    for label in labels:
        stamp = label["run"]
        if stamp not in cache:
            cache[stamp] = spans(json.loads(files[stamp].read_text()))
        steps = cache[stamp]
        before, after = steps[label["act"]][0], steps[label.get("after", label["act"])][1]
        exp, error = expect.parse(label["expect"])
        if error:
            raise SystemExit(f"{stamp} #{label['act']}: {error}")
        verdict = expect.verify(
            exp, before, after, set(label.get("typed", [])), label.get("tool", "click"), label.get("sent", "")
        )
        kinds = [k.split(" (")[0] for k in verdict.seen]  # the kinds only: a proof may quote the screen
        out.append({**label, "verdict": verdict.status, "seen": kinds})
    return out


def report(rows: list[dict], misses: bool = False) -> str:
    worked = [r for r in rows if r["worked"]]
    failed = [r for r in rows if not r["worked"]]
    met_worked = sum(r["verdict"] == expect.MET for r in worked)
    met_failed = [r for r in failed if r["verdict"] == expect.MET]
    agree = met_worked + len(failed) - len(met_failed)
    dones = [r for r in rows if r.get("false_done")]
    lines = [
        f"steps labelled: {len(rows)} ({len(worked)} worked, {len(failed)} did not)",
        f"agreement: {agree}/{len(rows)} = {agree / len(rows):.1%}",
        f"worked steps met: {met_worked}/{len(worked)} = {met_worked / max(1, len(worked)):.1%}",
        f"failed steps met (false met): {len(met_failed)}/{len(failed)}",
        f"known false dones met: {sum(r['verdict'] == expect.MET for r in dones)}/{len(dones)}",
    ]
    by_kind: dict[str, list[int]] = {}
    for r in rows:
        kind = expect.parse(r["expect"])[0].kind
        hit = (r["verdict"] == expect.MET) == r["worked"]
        by_kind.setdefault(kind, [0, 0])
        by_kind[kind][0] += hit
        by_kind[kind][1] += 1
    lines.append("by type: " + ", ".join(f"{k} {a}/{n}" for k, (a, n) in sorted(by_kind.items())))
    for r in dones:
        lines.append(f"false done {r['false_done']!r}: {r['verdict']}")
    if misses:
        for r in rows:
            if (r["verdict"] == expect.MET) != r["worked"]:
                seen = ", ".join(r["seen"]) or "-"
                lines.append(f"  miss {r['run']} #{r['act']} {r['expect']} worked={r['worked']}: {r['verdict']} ({seen})")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, default=RUNS)
    ap.add_argument("--labels", type=Path, default=LABELS)
    ap.add_argument("--misses", action="store_true")
    args = ap.parse_args()
    print(report(run(json.loads(args.labels.read_text()), args.runs), args.misses))


if __name__ == "__main__":
    main()
