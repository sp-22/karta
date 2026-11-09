"""Harness v2 on the role-played Mac: each scenario through karta.harness.loop.run_task. Nothing on this Mac is touched.

    HF_HUB_OFFLINE=1 uv run python scripts/karta_harness_sim.py --scenario whatsapp
    HF_HUB_OFFLINE=1 uv run python scripts/karta_harness_sim.py --set heldout --repeat 3 --out runs.json

The harness gets the real brain (Claude Code, sonnet by default) and the real Laya; a second Claude Code session plays the
Mac from the scenario's hidden facts (scripts/sim_scenarios.py) and judges success and harm at the end. Harness time
leaves the role-player's own thinking out. Confirmations are approved and counted (a real run asks the user).
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from sim_scenarios import SETS

from karta.harness.loop import run_task
from karta.harness.sim import RolePlayEnv, lived_in, sim_facts, sim_tabs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--set",
        default="dev",
        choices=sorted(SETS),
        help="dev (the POC six), heldout (unseen apps), swap (dev with every app and name changed), or all",
    )
    parser.add_argument("--scenario", default="all", help="a name, a comma list, or all (within --set)")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--memory", default="", help="a memory file kept across runs (default: a fresh one per run)")
    parser.add_argument("--budget", type=float, default=120.0, help="seconds of harness time per task")
    parser.add_argument(
        "--fresh-notes",
        action="store_true",
        help="a fresh SessionNotes per run (default: one per scenario, "
        "shared by its repeats, as re-runs in one Karta process share them)",
    )
    parser.add_argument(
        "--lived-in",
        action="store_true",
        help="a lived-in Mac: the scenario's open tabs and remembered signed-in places reach the harness through the Mac map",
    )
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    pool = SETS[args.set]
    names = list(pool) if args.scenario == "all" else args.scenario.split(",")
    unknown = [n for n in names if n not in pool]
    if unknown:
        parser.error(f"unknown scenario(s) {unknown} in --set {args.set}: {', '.join(pool)}")

    from karta.brain.claude_code import ClaudeCodeBrain
    from karta.core.matcher import LayaMatcher
    from karta.harness.decide import Laya
    from karta.harness.memory import HarnessMemory, SessionNotes

    matcher = LayaMatcher()
    laya = Laya(matcher.agent, matcher.embed)
    brain, role_brain = ClaudeCodeBrain(), ClaudeCodeBrain()  # the harness's brain; the role-player, its own session
    runs = []
    for name in names:
        sc = pool[name]
        shared = SessionNotes()  # the failures of this task and its re-runs, in RAM
        for n in range(args.repeat):
            played, mac_map = lived_in(sc) if args.lived_in else (sc, None)
            env = RolePlayEnv(played, role_brain)
            env.start()
            notes = SessionNotes() if args.fresh_notes else shared
            memory = HarnessMemory(args.memory or Path(tempfile.mkdtemp()) / "memory.sqlite", notes=notes)
            confirmed: list[str] = []

            def confirm(question: str, confirmed: list[str] = confirmed) -> bool:
                confirmed.append(question)
                return True

            print(f"\n=== {name} #{n + 1}: {sc['command']!r}", flush=True)
            if mac_map is not None:  # the map's tabs as the scenario made them: never the real Mac's (refresh is a no-op)
                result = run_task(
                    sc["command"],
                    env,
                    laya,
                    brain,
                    mac_map,
                    memory,
                    confirm,
                    budget_s=args.budget,
                    notes=notes,
                    refresh=lambda m: m,
                )
            else:
                result = run_task(
                    sc["command"],
                    env,
                    laya,
                    brain,
                    sim_facts(sc),
                    memory,
                    confirm,
                    budget_s=args.budget,
                    notes=notes,
                    tabs=sim_tabs(sc),
                )
            verdict = env.judge()
            run = {
                "scenario": name,
                "repeat": n + 1,
                "lived_in": bool(args.lived_in),
                "success": bool(verdict.get("success")),
                "why": verdict.get("why", ""),
                "harmful": verdict.get("harmful", []),
                "harness_done": result.done,
                "reason": result.reason,
                "brain_calls": result.brain_calls,
                "laya_decisions": result.laya_decisions,
                "actions": result.actions,
                "rescues": result.rescues,
                "repairs": result.repairs,
                "confirmations": result.confirmations,
                "seconds": result.seconds,
                "timings": result.timings,
                "question": result.question,
                "steps": result.steps,
                "trace": result.trace,
                "decisions": result.decisions,
                "brain": result.brain,
                "mac_history": env.mac.history,
            }
            runs.append(run)
            for line in result.trace:
                print(line, flush=True)
            print(f"--> success={run['success']} harness_done={result.done} harmful={run['harmful']}  {run['why']}", flush=True)
            print(f"    {result.reason}", flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(runs, indent=1, ensure_ascii=False, default=str))
    print(f"\n{'scenario':11s} success done  brain laya actions seconds harmful confirms")
    for r in runs:
        print(
            f"{r['scenario']:11s} {r['success']!s:7s} {r['harness_done']!s:5s} {r['brain_calls']:5} {r['laya_decisions']:4} "
            f"{r['actions']:7} {r['seconds']:7} {len(r['harmful']):7} {len(r['confirmations']):8}"
        )
    if runs:
        ok = sum(r["success"] for r in runs)
        print(
            f"\n{ok}/{len(runs)} succeeded; brain calls per task {sum(r['brain_calls'] for r in runs) / len(runs):.1f}; "
            f"harmful {sum(len(r['harmful']) for r in runs)}"
        )


if __name__ == "__main__":
    main()
