from karta.base.timing import phase


def test_phase_records_seconds_and_tolerates_none():
    timing: dict[str, float] = {}
    with phase(timing, "ocr"):
        pass
    assert timing["ocr"] >= 0.0
    with phase(None, "ocr"):
        pass


def test_phase_records_even_when_the_block_raises():
    timing: dict[str, float] = {}
    try:
        with phase(timing, "act"):
            raise ValueError("boom")
    except ValueError:
        pass
    assert "act" in timing
