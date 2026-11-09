from karta.harness import tell


class Brain:
    def __init__(self, reply="You have standup at ten.", fail=False):
        self.reply, self.fail, self.seen = reply, fail, []

    def stream_lines(self, system, user):
        self.seen.append(user)
        if self.fail:
            raise TimeoutError("slow")
        yield from self.reply.split("\n")


class HB:
    lines = True

    def __init__(self, brain):
        self.brain, self.requests, self.seconds, self.stats = brain, 0, 0.0, []

    @property
    def calls(self):
        return self.requests


FACTS = ["Standup at 10", "Lunch at 1", "Review at 4"]


def test_plain_no_brain():
    hb = HB(Brain())
    assert tell.word_answer("q", FACTS, hb, plain=True) == ("Three things: Standup at 10, Lunch at 1, and Review at 4.", False)
    assert hb.calls == 0


def test_plain_drops_urls():
    text, used = tell.word_answer("q", ["Alpha — https://a.example/x?y=1", "Beta - https://b.example"], HB(Brain()), plain=True)
    assert text == "Alpha and Beta." or text == "Two things: Alpha and Beta."
    assert "http" not in text and not used


def test_no_facts_no_call():
    hb = HB(Brain())
    text, used = tell.word_answer("q", [], hb)
    assert "couldn't find" in text and not used and hb.calls == 0


def test_brain_one_call_counted():
    brain = Brain("**You** have standup at ten https://x.example/a.")
    hb = HB(brain)
    text, used = tell.word_answer("what is on today?", FACTS, hb)
    assert used and hb.calls == 1 and len(brain.seen) == 1
    assert "http" not in text and "*" not in text
    assert "Standup at 10" in brain.seen[0] and "what is on today?" in brain.seen[0]


def test_brain_failure_falls_back():
    hb = HB(Brain(fail=True))
    text, used = tell.word_answer("q", FACTS, hb)
    assert not used and text.startswith("Three things") and hb.calls == 1


def test_caps():
    facts = [f"item {i} " + "x" * 500 for i in range(40)]
    brain = Brain("ok")
    tell.word_answer("q", facts, HB(brain))
    assert brain.seen[0].count("\n- ") <= tell.MAX_FACTS
    assert max(len(line) for line in brain.seen[0].splitlines()) < 400
    long = HB(Brain(" ".join(["word"] * 200)))
    assert len(tell.word_answer("q", FACTS, long)[0].split()) <= tell.MAX_WORDS + 1


def test_json_brain():
    class J:
        def json(self, system, user):
            return {"say": "Three items today."}

    hb = HB(J())
    hb.lines = False
    assert tell.word_answer("q", FACTS, hb) == ("Three items today.", True)
