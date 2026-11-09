"""Phase stopwatches: seconds per phase in a plain dict."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

# Neither of these is seconds: both describe the ocr phase rather than being phases of their own.
OCR_REGION_PCT = "ocr_region_pct"  # share of the capture handed to Vision
OCR_RECTS = "ocr_rects"  # how many rectangles it took, 0 for a full read or for nothing to read


@contextmanager
def phase(timing: dict[str, float] | None, name: str) -> Iterator[None]:
    """Record the seconds spent in the block under `name`. A None dict makes this a no-op."""
    started = time.perf_counter()
    try:
        yield
    finally:
        if timing is not None:
            timing[name] = round(time.perf_counter() - started, 3)
