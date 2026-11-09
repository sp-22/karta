"""Generalisation: product code, its comments and the rendered prompts never name what Karta was developed or will be
judged on, and every word list is registered with a reason (karta/harness/wordlists.py), the tailored ones only ever
fewer. Offline: it reads source files and the benches' data.

Banned names are derived, never hand-kept: the benches' apps and sites (their screens and case tags), the proper nouns,
hosts and quoted phrases of bench/everyday.md (the dev set) and bench/held-out.md (the held-out set), and the task texts
of the plan prompt bench. Allowed: the platform's own names (Apple's apps, the browsers) and the words inside a
registered safety, protocol or platform list."""

from __future__ import annotations

import ast
import functools
import io
import re
import tokenize
from pathlib import Path

from karta.harness import bench_cases, brain, decide, next_step_cases, prompt_bench, tell, wordlists
from karta.harness.facts import CHROMIUM, COMMON_WORD_APPS, HOST, SINGLE_PROFILE, site_of
from karta.harness.types import Item, Screen

REPO = Path(__file__).resolve().parents[1]
KARTA = REPO / "karta"
BENCHES = {"bench_cases", "carry_cases", "pin_cases", "repair_cases", "next_step_cases", "next_step", "prompt_bench", "sim",
           "replay", "snapshots", "wordlists"}  # fmt: skip
PRODUCT = sorted(
    p
    for p in [*KARTA.glob("harness/*.py"), *KARTA.glob("base/*.py"), *KARTA.glob("core/*.py")]
    if p.stem not in BENCHES and p.stem != "__init__"
)
BENCH_FILES = [KARTA / "harness" / f"{b}.py" for b in sorted(BENCHES - {"wordlists", "replay", "snapshots"})]
DOCS = [REPO / "bench" / "everyday.md", REPO / "bench" / "held-out.md"]
SAFE_HOSTS = re.compile(r"(^|\.)(example(\.[a-z]{2,3}){0,2}|test|localhost|invalid)$")
WEB_TLDS = {"com", "org", "net", "io", "app", "dev", "co", "in", "ai", "so", "us", "uk", "de", "es", "fr", "tv", "me"}
# The platform's own names: Apple's apps and the browsers (their names are the platform, never a site's or a bench's)
APPLE = {"finder", "terminal", "calendar", "notes", "reminders", "mail", "messages", "safari", "music", "maps", "photos",
         "contacts", "preview", "pages", "numbers", "keynote", "calculator", "system settings", "control center", "xcode",
         "textedit", "app store", "mac", "macos", "icloud", "apple", "finder's"}  # fmt: skip
NOT_NAMES = {"karta", "laya", "chrome", "brave", "google", "microsoft", "arc", "edge", "firefox", "vivaldi", "chromium",
             "desktop", "documents", "downloads", "english", "done", "false", "when"}  # Karta's own, the platform's  # fmt: skip
PLATFORM = APPLE | NOT_NAMES | set(COMMON_WORD_APPS) | {b.lower() for b in [*CHROMIUM, *SINGLE_PROFILE]}


DICTIONARY = Path("/usr/share/dict/words")  # the platform's word list: a word in it is common English, never a name
COMMON = {w for w in DICTIONARY.read_text().split() if w.islower()} if DICTIONARY.exists() else set()


def common(word: str) -> bool:
    """An everyday English word, not a name: "search", "slack" and "couldn't" are; "gmail" and "stickies" are not."""
    w = word.lower()  # a plural of an everyday word too ("results"), but never by "-ies": "stickies" is a name
    return "'" in w or w in COMMON or (w.endswith("s") and w[:-1] in COMMON) or (w.endswith("es") and w[:-2] in COMMON)


def _proper_nouns(text: str) -> set[str]:
    """Names in prose: a run of capitalised words that does not start a sentence ("Font Book", "Kindle Paperwhite"), as
    a phrase; a single one only when it is no everyday word ("Gmail", "Stickies")."""
    out = set()
    for sentence in re.split(r"(?<=[.!?:])\s+|\n|\*\*", text):
        words = re.findall(r"[A-Za-z][\w'.-]*[\w]|[A-Za-z]", sentence)
        run: list[str] = []
        for w in [*words[1:], ""]:
            if w and w[0].isupper() and not w.isupper():
                run.append(w)
                continue
            if len(run) > 1 and any(len(w) > 3 for w in run):
                out.add(" ".join(run).lower())
            elif run and len(run[0]) >= 4 and not common(run[0]):
                out.add(run[0].lower())
            run = []
    return out


def _site_labels(text: str) -> set[str]:
    out = set()
    for m in HOST.finditer(text):
        host = m[2].lower()
        if SAFE_HOSTS.search(host.split(":")[0]) or host.rsplit(".", 1)[-1] not in WEB_TLDS | {"org", "wiki"}:
            continue
        out.add(host)
        label = site_of(host).split(".")[0]
        if len(label) >= 4 and not common(label):
            out.add(label)
    return out


def _screens(value, seen: set[int]):
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, Screen):
        yield value
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _screens(v, seen)


@functools.cache
def banned() -> frozenset[str]:
    """Every name the benches, the dev set and the held-out set use, minus the platform's own names."""
    names: set[str] = set()
    screens: list[Screen] = []
    for case in bench_cases.all_cases():
        if case.app and len(case.app) >= 4 and not common(case.app):
            names.add(case.app.lower())
        screens += list(_screens(case.args, set()))
    for case in prompt_bench.CASES:
        screens.append(case.screen)
        names |= _proper_nouns(case.task) | _site_labels(case.task)
    for case in next_step_cases.cases():
        screens += list(_screens(case.screen, set()))
    for scr in screens:
        if len(scr.app) >= 4 and not common(scr.app):
            names.add(scr.app.lower())
        names |= _site_labels(scr.url or "")
        names |= {label for i in scr.items if i.url for label in _site_labels(i.url)}
    for path in BENCH_FILES:  # every host a bench's screens or plans hold, and every app its screens are of
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                names |= _site_labels(node.value)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("Screen", "_page", "page") and node.args:
                app = node.args[0]
                if isinstance(app, ast.Constant) and isinstance(app.value, str) and len(app.value) >= 4:
                    names |= {app.value.lower()} - {w for w in [app.value.lower()] if common(w)}
    for doc in DOCS:
        text = doc.read_text()
        names |= _proper_nouns(text) | _site_labels(text)
        quoted = re.findall(r'"([^"\n]{6,60})"', text)  # its quoted phrases that hold a name ("Karta held-out: ...")
        names |= {q.lower() for q in quoted if " " in q and not all(common(w) for w in re.findall(r"[\w']+", q))}
        names |= {q.split(": ", 1)[1].lower() for q in quoted if ": " in q and " " in q.split(": ", 1)[1]}  # its message
    return frozenset(n for n in names if n not in PLATFORM and not n.startswith(tuple(PLATFORM)) and len(n) >= 4)


def registered_lines(path: Path) -> set[int]:
    """The source lines of the registered safety, protocol and platform lists of `path`: their words are allowed."""
    keep = {
        k.split(".", 1)[1] for k, (cat, _) in wordlists.REGISTRY.items() if k.split(".")[0] == path.stem and cat != "tailored"
    }
    lines: set[int] = set()
    for node in ast.parse(path.read_text()).body:
        targets = [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)]
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets.append(node.target.id)
        if set(targets) & keep:
            lines |= set(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


def texts(path: Path):
    """(line, text) of every string literal and every comment in `path`."""
    source = path.read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            yield tok.start[0], tok.string


def prompts() -> dict[str, str]:
    """Every prompt the brain or Laya is given, rendered: the plan, rescue and end prompts, tell's, and the questions
    and options decide.py's templates produce on a made-up screen."""
    out = {name: getattr(brain, name) for name in dir(brain) if name.endswith(("_SYS", "_SYS_V2", "_SYS_JSON"))}
    out["tell.SYSTEM"] = tell.SYSTEM
    seen: list[str] = []

    class Agent:
        def system_one(self, state, spec):
            q = spec["q"]
            seen.append(" ".join([q["instructions"], *q["criteria"], *map(str, state.values())]))
            return {"answers": {"q": {"probabilities": dict.fromkeys(q["criteria"], 1.0 / len(q["criteria"]))}}}

    lay = decide.Laya(Agent(), gates={})
    scr = Screen("Pebbleapp", [Item("Open", "button"), Item("Name", "field"), Item("A row", "row")], url="https://a.example/")
    lay.page_state("Pebbleapp", scr), lay.screen_kind(scr), lay.target("open it", scr.items)
    lay.pick_ok("open it", scr.items[0]), lay.name_match("a row", "row", scr.items)
    lay.dialog_answer("close it", scr.items), lay.dialog_role("open it", scr.items), lay.outcome("a row", scr)
    lay.collect_fit("rows", scr.items[2]), lay.place("Pebbleapp", ["Pebbleapp app"])
    out["decide (rendered)"] = "\n".join(seen)
    return out


def _hit(word: str, text: str) -> bool:
    return re.search(rf"(?<![\w.-]){re.escape(word)}(?![\w-])", text, re.I) is not None


def test_the_banned_names_are_derived_and_include_the_held_out_set():
    names = banned()
    for held_out in ("stickies", "font book", "voice memos", "wiktionary", "codeberg", "marmiton", "timeanddate"):
        assert any(held_out in n for n in names), held_out
    assert "notes" not in names and "safari" not in names  # the platform's own


def test_no_bench_dev_or_held_out_name_in_product_code_its_comments_or_its_prompts():
    names = sorted(banned(), key=len, reverse=True)
    hits = []
    for path in PRODUCT:
        allowed = registered_lines(path)
        for line, text in texts(path):
            if line in allowed or "generalise-ok" in text:
                continue
            hits += [f"{path.relative_to(REPO)}:{line}: {w!r}" for w in names if _hit(w, text)]
    for name, text in prompts().items():
        hits += [f"{name}: {w!r}" for w in names if _hit(w, text)]
    assert not hits, "\n".join(sorted(set(hits)))


def test_hosts_in_product_code_are_made_up():
    bad = []
    for path in PRODUCT:
        for line, text in texts(path):
            for m in HOST.finditer(text):
                host = m[2].lower().split(":")[0]
                if text[m.end(2) : m.end(2) + 1].isalnum() or text[m.end(2) : m.end(2) + 1] == "_":
                    continue  # code ("facts.in_the_way"), not a host
                if host.rsplit(".", 1)[-1] in WEB_TLDS and not SAFE_HOSTS.search(host):
                    bad.append(f"{path.relative_to(REPO)}:{line}: {host}")
    for name, text in prompts().items():
        bad += [
            f"{name}: {m[2]}" for m in HOST.finditer(text) if m[2].rsplit(".", 1)[-1] in WEB_TLDS and not SAFE_HOSTS.search(m[2])
        ]
    assert not bad, "\n".join(bad)


LOWER_WORD = re.compile(r"[a-z][a-z' -]{1,40}")


def is_word_list(value: ast.AST) -> bool:
    """Three or more lowercase words, or a regex with three or more alternatives that hold letters."""
    strings = [n.value for n in ast.walk(value) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    if sum(bool(LOWER_WORD.fullmatch(s)) for s in strings) >= 3:
        return True
    patterns = [s for s in strings if s.count("|") >= 2 and "\n" not in s and len(s) < 400]  # a prompt's text is no regex
    return any(len([a for a in re.split(r"[|()]", s) if re.search(r"[a-z]{3,}", a)]) >= 3 for s in patterns)


def word_lists() -> set[str]:
    found = set()
    for path in PRODUCT:
        for node in ast.parse(path.read_text()).body:
            value = getattr(node, "value", None)
            targets = [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)]
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets.append(node.target.id)
            if value is not None and targets and is_word_list(value):
                found |= {f"{path.stem}.{t}" for t in targets}
    return found


def defined() -> set[str]:
    out = set()
    for path in PRODUCT:
        for node in ast.parse(path.read_text()).body:
            out |= {f"{path.stem}.{t.id}" for t in getattr(node, "targets", []) if isinstance(t, ast.Name)}
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                out.add(f"{path.stem}.{node.target.id}")
    return out


def test_every_word_list_is_registered_and_every_registered_one_exists():
    missing = sorted(word_lists() - set(wordlists.REGISTRY))
    assert not missing, f"register these in karta/harness/wordlists.py (or replace them with structure): {missing}"
    stale = sorted(set(wordlists.REGISTRY) - defined())
    assert not stale, f"gone from the code, so gone from the registry: {stale}"
    assert all(cat in ("safety", "protocol", "platform", "format", "tailored") for cat, _ in wordlists.REGISTRY.values())


def test_the_tailored_lists_only_ever_get_fewer():
    tailored = [k for k, (cat, _) in wordlists.REGISTRY.items() if cat == "tailored"]
    assert len(tailored) <= wordlists.TAILORED_CEILING, tailored


def test_no_product_module_imports_a_bench():
    for path in PRODUCT:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom | ast.Import):
                mods = [getattr(node, "module", None) or ""] + [a.name for a in node.names]
                assert not any(m.split(".")[-1] in BENCHES - {"snapshots", "replay"} for m in mods), (path.name, mods)
