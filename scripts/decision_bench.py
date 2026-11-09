"""Benchmark Laya's decisions D1 to D8 and write each decision's template and policy.

    HF_HUB_OFFLINE=1 uv run python scripts/decision_bench.py                    # every decision and template, writes gates.json
    HF_HUB_OFFLINE=1 uv run python scripts/decision_bench.py --only "D2 page state" --show
    HF_HUB_OFFLINE=1 uv run python scripts/decision_bench.py --model typed-decisions --no-write
    HF_HUB_OFFLINE=1 uv run python scripts/decision_bench.py --no-write --baseline old_gates.json   # before vs after

Prints accuracy and median ms per decision and per template, on the tuning split and on the held-out split (apps
and sites seen nowhere in tuning: the number that counts), and how many cases each app or site gave. A template is
chosen by what its fitted policy lets the harness act on: held-out coverage at the target precision, then held-out
accuracy, then tuning accuracy. One that wins only on the scenario apps loses.

The policy of a decision: act when p1 >= floor and p1 - p2 >= margin, or p1 >= high, in two risk tiers.
Risky (cannot be undone): fitted on the held-out split for about 95% precision (right answers among those acted on)
with the most coverage (share of cases acted on). Reversible (checked right after; a rejected pick is dropped and the
next taken): a moderate floor and small margin fitted for accuracy after one elimination round (accuracy@2). The
tuning split breaks ties and is reported alongside as the check that it was not fitted to. "Zero
wrong above one gate" left right answers to the ladder ("Got it" at 0.70 against 0.91). Laya runs on this Mac;
nothing is sent anywhere.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from karta.harness.bench_cases import ITEM_DECISIONS, METHODS, SCENARIO_APPS, Case, all_cases
from karta.harness.decide import GATES, NONE, PROFILE, Laya, _short

PRECISION = 0.95  # of the answers acted on, this share must be right (held-out)
FLOORS = [round(0.3 + 0.05 * n, 2) for n in range(14)]  # 0.30 .. 0.95
MARGINS = [round(0.05 * n, 2) for n in range(17)]  # 0.00 .. 0.80
HIGHS = [0.85, 0.9, 0.95, 0.98, 1.01]  # 1.01: no shortcut
# Reversible actions: a moderate floor and a small margin, fitted for accuracy after one elimination round.
REVERSIBLE_FLOORS = [round(0.3 + 0.05 * n, 2) for n in range(7)]  # 0.30 .. 0.60
REVERSIBLE_MARGINS = [round(0.05 * n, 2) for n in range(5)]  # 0.00 .. 0.20
CHECKS = ("D4 pick check", "D7 outcome")  # they are the checks: nothing verifies them, so no second round
SECOND_ROUND = 0.5  # a case right only after one elimination round is worth half: it costs a check and a re-ask


def worth(r: Run) -> float:
    """What acting on a run is worth: 1 right at once, SECOND_ROUND right after one round, -1 wrong after both
    (an unasked case is 0: the ladder takes it)."""
    return 1.0 if r.ok else SECOND_ROUND if r.ok2 else -1.0


@dataclass
class Run:
    case: Case
    best: str
    p: float
    p2: float
    ok: bool
    ms: float
    ranked: list
    ok2: bool = False  # the top pick or, once a check rejects it, the second is right


def second_round(case: Case, ranked: list) -> bool:
    """After a check rejects the top pick, is the next option right? Only with three or more options: with two the
    second is forced (and a check is itself a Laya answer), so it counts as the top pick alone."""
    others = [o for o, _ in ranked[1:] if PROFILE.sub("", o) != PROFILE.sub("", ranked[0][0])]
    return (
        case.decision not in CHECKS
        and len({PROFILE.sub("", o) for o, _ in ranked}) > 2
        and bool(others)
        and correct(case, others[0])
    )


def correct(case: Case, best: str) -> bool:
    """Right when `best` is an answer, or, for an item decision, the label of an item an answer names: "Pull
    requests 3 (top)" is the tab "Pull requests 3". An answer written as a label ("ADD (Kurkure Masala Munch)")
    names one item of several with the same name and must match exactly."""
    if best in case.answers:
        return True
    if case.decision in ITEM_DECISIONS and best != NONE:
        names = {_short(a) for a in case.answers}
        return best in names or re.sub(r" \([^()]*\)( #\d+)?$|\s#\d+$", "", best) in names
    return False


def run(laya: Laya, cases: list[Case]) -> list[Run]:
    out = []
    for case in cases:
        choice = getattr(laya, METHODS[case.decision])(*case.args)
        top = PROFILE.sub("", choice.best)
        p2 = next((p for o, p in choice.ranked[1:] if PROFILE.sub("", o) != top), 0.0)  # a different site, not a profile
        ok = correct(case, choice.best)
        ranked = choice.ranked[:8]
        out.append(Run(case, choice.best, choice.ranked[0][1], p2, ok, choice.ms, ranked, ok or second_round(case, ranked)))
    return out


def acts(policy: dict, r: Run) -> bool:
    """The same rule as Laya.acts, on a recorded run (small slack for rounding)."""
    need = min(policy["high"], max(policy["floor"], r.p2 + policy["margin"]))
    return r.p >= need - 1e-9


def scores(runs: list[Run], policy: dict) -> tuple[float, float, float]:
    """(precision, coverage, accuracy): right among acted on (1.0 when none), acted on among all, right among all."""
    acted = [r for r in runs if acts(policy, r)]
    precision = sum(r.ok for r in acted) / len(acted) if acted else 1.0
    return precision, len(acted) / len(runs) if runs else 0.0, sum(r.ok for r in runs) / len(runs) if runs else 0.0


def reversible_scores(runs: list[Run], policy: dict) -> tuple[float, float, float]:
    """(coverage, accuracy@1, accuracy@2) of a reversible policy: acted on among all; among those acted on, the top
    pick right; the top pick or the second (after a check rejects the first) right."""
    acted = [r for r in runs if acts(policy, r)]
    if not acted:
        return 0.0, 1.0, 1.0
    return len(acted) / len(runs), sum(r.ok for r in acted) / len(acted), sum(r.ok2 for r in acted) / len(acted)


def fit_reversible(held: list[Run], tune: list[Run]) -> dict:
    """The reversible policy (floor 0.30 to 0.60, margin up to 0.20) with the best held-out net `worth` of the cases
    it acts on. Tuning breaks ties, then the middle of the tied policies."""
    held = held or tune

    def net(runs: list[Run], pol: dict) -> float:
        return sum(worth(r) for r in runs if acts(pol, r))

    grid = [{"floor": f, "margin": m, "high": h} for f in REVERSIBLE_FLOORS for m in REVERSIBLE_MARGINS for h in HIGHS if h > f]
    top = max((net(held, pol), net(tune, pol)) for pol in grid)
    tied = sorted(
        (pol for pol in grid if (net(held, pol), net(tune, pol)) == top),
        key=lambda pol: (pol["floor"] + pol["margin"], -pol["high"]),
    )
    return tied[len(tied) // 2]


def reversible_summary(runs: list[Run], policy: dict) -> str:
    coverage, at1, at2 = reversible_scores(runs, policy)
    return f"acts on {sum(acts(policy, r) for r in runs)} ({coverage:4.0%}): accuracy@1 {at1:5.1%}, @2 {at2:5.1%}"


def fit_policy(held: list[Run], tune: list[Run]) -> dict:
    """The policy with the most held-out coverage at PRECISION or better on held-out (the best precision when none
    reaches it); then tuning precision at PRECISION and tuning coverage. Policies that score the same on every case
    differ only between cases, so the middle one of them is taken: as far from the last right answer it leaves out as
    from the first wrong one it would take. With no held-out runs, tuning stands in."""
    held = held or tune

    def key(pol: dict) -> tuple:
        hp, hc, _ = scores(held, pol)
        tp, tc, _ = scores(tune, pol)
        return (hp >= PRECISION, hc if hp >= PRECISION else hp, tp >= PRECISION, tc)

    grid = [{"floor": f, "margin": m, "high": h} for f in FLOORS for m in MARGINS for h in HIGHS if h > f]
    top = max(key(pol) for pol in grid)
    tied = sorted((pol for pol in grid if key(pol) == top), key=lambda pol: (pol["floor"] + pol["margin"], -pol["high"]))
    return tied[len(tied) // 2]


def summary(runs: list[Run], policy: dict) -> str:
    precision, coverage, accuracy = scores(runs, policy)
    acted = sum(acts(policy, r) for r in runs)
    wrong = sum(acts(policy, r) and not r.ok for r in runs)
    return (
        f"acc {accuracy:5.1%} ({sum(r.ok for r in runs)}/{len(runs)})  median {statistics.median(r.ms for r in runs):5.1f} ms"
        f"  acts on {acted} ({coverage:4.0%}), {wrong} wrong: precision {precision:5.1%}"
    )


def rank(runs: list[Run], held_runs: list[Run], risky: dict, reversible: dict) -> tuple:
    """A template's standing on held-out: cases that end right after one round under the reversible policy (most
    actions), then coverage at PRECISION under the risky one, then accuracy; tuning accuracy and speed break ties."""
    held = held_runs or runs
    right = sum(worth(r) for r in held if acts(reversible, r))
    hp, hc, ha = scores(held, risky)
    return (right, hp >= PRECISION, hc, ha, scores(runs, risky)[2], -statistics.median(r.ms for r in runs))


def describe(policy: dict) -> str:
    high = "none" if policy["high"] > 1 else f"{policy['high']:.2f}"
    return f"floor {policy['floor']:.2f}, margin {policy['margin']:.2f}, high {high}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="english", choices=["english", "typed-decisions", "multilingual"])
    parser.add_argument("--only", action="append", help="a decision, e.g. 'D2 page state' (repeatable)")
    parser.add_argument("--show", action="store_true", help="print each wrong answer")
    parser.add_argument("--no-write", action="store_true", help="do not write gates.json")
    parser.add_argument("--baseline", help="an older gates.json: also print its single gates' numbers on the chosen template")
    args = parser.parse_args()

    import laya_mlx

    from karta.decider.laya import cached_embed, load_agent

    agent = load_agent(args.model)
    embed = cached_embed(laya_mlx.embed_fn_from_agent(agent))
    cases = [c for c in all_cases() if not args.only or c.decision in args.only]
    gates = json.loads(GATES.read_text()) if GATES.exists() else {}
    baseline = json.loads(Path(args.baseline).read_text()) if args.baseline else {}
    for decision, templates in Laya.TEMPLATES.items():
        tune = [c for c in cases if c.decision == decision and c.split == "tune"]
        held = [c for c in cases if c.decision == decision and c.split == "held"]
        if not tune:
            continue
        print(
            f"\n{decision} (tuning {len(tune)} cases, {sum(c.source == 'poc' for c in tune)} from POC runs; held-out {len(held)})"
        )
        for name, split in (("tuning", tune), ("held-out", held)):
            print(f"  {name} apps: " + ", ".join(f"{a} {n}" for a, n in Counter(c.app for c in split).most_common()))
        scenario = sum(any(s in c.app.lower() for s in SCENARIO_APPS) for c in tune)
        if scenario > len(tune) / 3 or max(Counter(c.app for c in tune).values()) > 0.15 * len(tune):
            print("  warning: the tuning split leans on a few apps")
        results = {}
        for template in templates:
            laya = Laya(agent, embed, gates={}, templates={decision: template})
            run(laya, tune[:1])  # warm the prefix and embeddings once
            runs, held_runs = run(laya, tune), run(laya, held)
            policy, reversible = fit_policy(held_runs, runs), fit_reversible(held_runs, runs)
            results[template] = (runs, held_runs, policy, reversible)
            print(f"  {template:<15} risky {describe(policy)}; reversible {describe(reversible)}")
            print(f"  {'':<15} tune {summary(runs, policy)}")
            print(f"  {'':<15}      reversible {reversible_summary(runs, reversible)}")
            if held_runs:
                print(f"  {'':<15} held {summary(held_runs, policy)}")
                print(f"  {'':<15}      reversible {reversible_summary(held_runs, reversible)}")
            if args.show:
                for r in runs + held_runs:
                    if not r.ok:
                        print(
                            f"      x [{r.case.split}] {r.case.name}: {r.best!r} p={r.p:.2f}"
                            f"{' ACTED' if acts(policy, r) else ''}  want {sorted(r.case.answers)[:2]}  {r.ranked}"
                        )
        best = max(results, key=lambda t: rank(*results[t]))
        runs, held_runs, policy, reversible = results[best]
        (hp, hc, ha), (tp, tc, ta) = scores(held_runs, policy), scores(runs, policy)
        (hrc, hr1, hr2), (trc, tr1, tr2) = reversible_scores(held_runs, reversible), reversible_scores(runs, reversible)
        old = (baseline.get(decision) or {}).get("gate")
        if old is not None:
            single = {"floor": float(old), "margin": 0.0, "high": float(old)}
            print(f"  baseline gate {old:.2f} on {best}: held {summary(held_runs, single)}")
            print(f"  {'':<27} tune {summary(runs, single)}")
            print(f"  {'':<27} held as reversible: {reversible_summary(held_runs, single)}")
        gates[decision] = {
            "template": best,
            "policy": reversible,  # the reversible tier: Choice.gate and Choice.sure follow it
            "risky_policy": policy,
            "gate": reversible["floor"],  # the reversible floor: no answer below it is acted on
            "held_out": {
                "reversible": {"coverage": round(hrc, 3), "accuracy@1": round(hr1, 3), "accuracy@2": round(hr2, 3)},
                "risky": {"precision": round(hp, 3), "coverage": round(hc, 3)},
                "accuracy": round(ha, 3),
            },
            "tuning": {
                "reversible": {"coverage": round(trc, 3), "accuracy@1": round(tr1, 3), "accuracy@2": round(tr2, 3)},
                "risky": {"precision": round(tp, 3), "coverage": round(tc, 3)},
                "accuracy": round(ta, 3),
            },
            "tuning_accuracy": round(ta, 3),
            "held_out_accuracy": round(ha, 3) if held_runs else None,
            "median_ms": round(statistics.median(r.ms for r in runs + held_runs), 1),
            "cases": {"tuning": len(runs), "held_out": len(held_runs)},
            "chosen_by": (
                f"reversible: best held-out net after one elimination round; risky: most held-out coverage at {PRECISION:.0%}"
                " precision; template: reversible net, then risky coverage, then accuracy"
            ),
            "model": args.model,
        }
        print(f"  -> {best}: reversible {describe(reversible)}; risky {describe(policy)}")
    if not args.no_write:
        GATES.write_text(json.dumps(gates, indent=2, ensure_ascii=False) + "\n")
        print(f"\nwrote {GATES}")


if __name__ == "__main__":
    main()
