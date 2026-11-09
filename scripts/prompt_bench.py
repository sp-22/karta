"""The plan prompt stress bench (karta/harness/prompt_bench.py): run the real brain on the 40 tasks, then score.

Nothing here touches the Mac: made-up screens and Mac map, and the Claude Code CLI for the brain (text only).

    uv run python scripts/prompt_bench.py run --prompt v1 --n 3 --label v1
    uv run python scripts/prompt_bench.py run --prompt v2 --n 3 --label v2-r1 [--cases n01,w03]
    uv run python scripts/prompt_bench.py report v1 v2-r1 [--worst 5]
    uv run python scripts/prompt_bench.py pairs --prompt v1 --n 3 --label mem-v1     (with and without memory)
    uv run python scripts/prompt_bench.py pairs-report mem-v1
    uv run python scripts/prompt_bench.py ab --label ab-1          (the memory A/B bench: at most 40 brain calls)

Each run is written to <dir>/<label>.jsonl (a record per plan) with the system prompt it used in <label>.prompt.txt;
a pairs run to <label>-memory.jsonl and <label>-none.jsonl.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from karta.harness import prompt_bench as pb
from karta.harness.brain import PLAN_SYS, PLAN_SYS_V2

DIR = Path.home() / "Desktop" / "Projects" / "karta-research" / "designs" / "prompt-stress"


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--prompt", choices=("v1", "v2"), required=True)
    r.add_argument("--n", type=int, default=3)
    r.add_argument("--label", required=True)
    r.add_argument("--cases", default="")
    r.add_argument("--workers", type=int, default=6)
    r.add_argument("--dir", type=Path, default=DIR)
    m = sub.add_parser("pairs")
    m.add_argument("--prompt", choices=("v1", "v2"), required=True)
    m.add_argument("--n", type=int, default=3)
    m.add_argument("--label", required=True)
    m.add_argument("--workers", type=int, default=6)
    m.add_argument("--dir", type=Path, default=DIR)
    ab = sub.add_parser("ab")
    ab.add_argument("--prompt", choices=("v1", "v2"), default="v2")
    ab.add_argument("--n", type=int, default=3)
    ab.add_argument("--label", required=True)
    ab.add_argument("--workers", type=int, default=3)
    ab.add_argument("--dir", type=Path, default=DIR)
    mr = sub.add_parser("pairs-report")
    mr.add_argument("labels", nargs="+")
    mr.add_argument("--dir", type=Path, default=DIR)
    p = sub.add_parser("report")
    p.add_argument("labels", nargs="+")
    p.add_argument("--worst", type=int, default=5)
    p.add_argument("--dir", type=Path, default=DIR)
    args = ap.parse_args()

    if args.cmd == "run":
        wanted = set(filter(None, args.cases.split(",")))
        cases = [c for c in pb.CASES if not wanted or c.id in wanted]
        args.dir.mkdir(parents=True, exist_ok=True)
        (args.dir / f"{args.label}.prompt.txt").write_text(PLAN_SYS_V2 if args.prompt == "v2" else PLAN_SYS)
        pb.run(args.prompt, args.n, cases, args.dir / f"{args.label}.jsonl", args.workers)
        return
    if args.cmd == "pairs":
        args.dir.mkdir(parents=True, exist_ok=True)
        cases = [pair.case for pair in pb.PAIRS]
        for with_memory, tag in ((True, "memory"), (False, "none")):
            pb.run(
                args.prompt, args.n, cases, args.dir / f"{args.label}-{tag}.jsonl", args.workers, plan=pb.plan_pair(with_memory)
            )
        return
    if args.cmd == "ab":
        from karta.brain.claude_code import ClaudeCodeBrain

        args.dir.mkdir(parents=True, exist_ok=True)
        cases = [pb.PAIR_BY_ID[i].case for i in pb.AB]
        budget = pb.Budget(ClaudeCodeBrain)
        for with_memory, tag in ((True, "memory"), (False, "none")):
            out = args.dir / f"{args.label}-{tag}.jsonl"
            pb.run(args.prompt, args.n, cases, out, args.workers, make_brain=budget, plan=pb.plan_pair(with_memory))
        print(f"brain calls: {budget.used} of {budget.limit}")
        print(pb.memory_table({f"{args.label} {t}": pb.load(args.dir / f"{args.label}-{t}.jsonl") for t in ("memory", "none")}))
        return
    if args.cmd == "pairs-report":
        tags = ("memory", "none")
        print(pb.memory_table({f"{label} {t}": pb.load(args.dir / f"{label}-{t}.jsonl") for label in args.labels for t in tags}))
        return
    columns = {label: pb.load(args.dir / f"{label}.jsonl") for label in args.labels}
    print(pb.table(columns))
    for label, recs in columns.items():
        print(f"\nWorst checkpoints, {label} (text | field | times | cases):")
        for text, field, times, cases in pb.worst(recs, args.worst):
            print(f"- {text!r} | {field} | {times} | {cases}")


if __name__ == "__main__":
    main()
