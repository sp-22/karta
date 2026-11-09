"""Next-step bench: once the right app or site is open, can Laya alone pick the next action from the screen?

    HF_HUB_OFFLINE=1 uv run --no-sync python scripts/next_step_bench.py                  # seed cases, every variant
    HF_HUB_OFFLINE=1 uv run --no-sync python scripts/next_step_bench.py --show           # and each miss
    HF_HUB_OFFLINE=1 uv run --no-sync python scripts/next_step_bench.py --recorded ~/Library/Application\\ Support/karta/snapshots
    HF_HUB_OFFLINE=1 uv run --no-sync python scripts/next_step_bench.py --shuffle 1      # options in a random order

Cases: the seed set (karta.harness.next_step_cases), plus recorded runs when a folder is given (karta --record).
Options are built by code from the screen (karta.harness.next_step.build): click / type into each actionable item,
and "the task is done", "scroll down", "something is in the way"; one Laya question per case. Variants:
  context         repeated labels carry their row or region ("click ADD (row: Tangy Tomato Chips)"); the page in the state
  bare            options are the bare labels ("click ADD #2"); the page (with its rows) still in the state
  context-nopage  context options, the task alone as the state
  bare-nopage     bare options, the task alone
Scores: offered (the right option survived the budget), top-1, top-2, sure-rate and accuracy when sure (the default
reversible policy: no gates.json entry exists for this decision; D3 target's policy shown alongside), per family.
Laya runs on this Mac; nothing is clicked or sent.
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from dataclasses import dataclass

from karta.harness.next_step import INSTRUCTIONS, Case, ask, load_recorded
from karta.harness.next_step_cases import cases as seed_cases

VARIANTS = {
    "context": (True, True),
    "bare": (False, True),
    "context-nopage": (True, False),
    "bare-nopage": (False, False),
}


@dataclass
class Run:
    case: Case
    offered: bool
    top1: bool
    top2: bool
    sure: bool
    sure_d3: bool
    p: float
    best: str
    right: list[str]
    ranked: list
    ms: float


def run_case(laya, case: Case, context: bool, with_page: bool, shuffle: int | None, template: str = "next") -> Run:
    choice, opts = ask(laya, case.task, case.screen, context=context, with_page=with_page, shuffle=shuffle, template=template)
    key = {label: action.key for label, action in opts.options.items()}
    right = [label for label, k in key.items() if k in case.answers]
    labels = [o for o, _ in choice.ranked]
    top1 = bool(labels) and key.get(labels[0]) in case.answers
    top2 = any(key.get(o) in case.answers for o in labels[:2])
    d3 = laya.policy("D3 target")
    p1 = choice.ranked[0][1] if choice.ranked else 0.0
    p2 = choice.ranked[1][1] if len(choice.ranked) > 1 else 0.0
    sure_d3 = p1 >= min(d3["high"], max(d3["floor"], p2 + d3["margin"]))
    return Run(case, bool(right), top1, top2, choice.sure, sure_d3, p1, choice.best, right, choice.ranked[:4], choice.ms)


def pct(n: int, d: int) -> str:
    return f"{n / d:5.1%}" if d else "  -  "


def line(runs: list[Run]) -> str:
    n = len(runs)
    sure = [r for r in runs if r.sure]
    sure3 = [r for r in runs if r.sure_d3]
    return (
        f"n {n:>3}  offered {pct(sum(r.offered for r in runs), n)}  top-1 {pct(sum(r.top1 for r in runs), n)}"
        f"  top-2 {pct(sum(r.top2 for r in runs), n)}  sure {pct(len(sure), n)} -> right {pct(sum(r.top1 for r in sure), len(sure))}"
        f"  (D3 policy: sure {pct(len(sure3), n)} -> right {pct(sum(r.top1 for r in sure3), len(sure3))})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="english", choices=["english", "typed-decisions", "multilingual"])
    parser.add_argument("--recorded", help="a folder of snapshot files (karta --record) to add as cases")
    parser.add_argument("--no-seed", action="store_true", help="recorded cases only")
    parser.add_argument("--variant", action="append", choices=list(VARIANTS), help="only these variants (repeatable)")
    parser.add_argument("--shuffle", type=int, help="offer item options in a random order (this seed), not screen order")
    parser.add_argument("--template", default="next", choices=list(INSTRUCTIONS), help="the question's wording")
    parser.add_argument("--show", action="store_true", help="print each miss of the first variant")
    args = parser.parse_args()

    import laya_mlx

    from karta.decider.laya import cached_embed, load_agent
    from karta.harness.decide import Laya

    agent = load_agent(args.model)
    laya = Laya(agent, cached_embed(laya_mlx.embed_fn_from_agent(agent)))
    cases = ([] if args.no_seed else seed_cases()) + (load_recorded(args.recorded) if args.recorded else [])
    if not cases:
        raise SystemExit("no cases")
    print(
        f"{len(cases)} cases: "
        + ", ".join(f"{f} {sum(c.family == f for c in cases)}" for f in dict.fromkeys(c.family for c in cases))
    )
    print(f"policy for sure: {laya.policy('N1 next step')} (default); D3 target: {laya.policy('D3 target')}")
    ask(laya, cases[0].task, cases[0].screen)  # warm up
    for name in args.variant or list(VARIANTS):
        context, with_page = VARIANTS[name]
        runs = [run_case(laya, c, context, with_page, args.shuffle, args.template) for c in cases]
        print(f"\n{name}: {line(runs)}  median {statistics.median(r.ms for r in runs):.0f} ms")
        families = defaultdict(list)
        for r in runs:
            families[r.case.family].append(r)
        for family, fr in families.items():
            print(f"  {family:<10} {line(fr)}")
        if args.show and name == (args.variant or list(VARIANTS))[0]:
            for r in runs:
                if not r.top1:
                    ranked = ", ".join(f"{o!r} {p:.2f}" for o, p in r.ranked)
                    print(
                        f"    x {r.case.name}: chose {r.best!r} ({r.p:.2f}{' SURE' if r.sure else ''}); want {r.right}; {ranked}"
                    )


if __name__ == "__main__":
    main()
