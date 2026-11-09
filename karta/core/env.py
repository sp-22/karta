"""What the loop needs from a screen: read its elements, click one, type into one, tell if it changed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

EDITABLE_ROLES = (
    "field",  # karta.base perception's word for text fields, search fields, text areas and combo boxes
    "text field",
    "search field",
    "text area",
    "combo box",
    "AXTextField",
    "AXTextArea",
    "AXSearchField",
    "AXComboBox",
)
OCR_ROLE = "text on screen"  # read from pixels: may be a field's placeholder ("Search mail"), so typing may target it


@dataclass(frozen=True)
class Element:
    label: str
    role: str = ""  # "" when the element was read by OCR and declares no role

    @property
    def seen_by_ocr(self) -> bool:
        return self.role == OCR_ROLE

    @property
    def editable(self) -> bool:
        return self.role in EDITABLE_ROLES

    def describe(self) -> str:
        return f"{self.label} ({self.role})" if self.role else self.label


class Env(Protocol):
    def observe(self) -> list[Element]: ...

    def click(self, element: Element) -> None: ...

    def type_into(self, element: Element, text: str) -> None: ...

    def type_text(self, text: str) -> None:
        """Type at the keyboard focus."""
        ...

    def press(self, keys: str) -> None:
        """A key or combo: "return", "escape", "cmd+n"."""
        ...

    def open(self, target: str) -> None:
        """An app by name ("Notes") or a site ("video.example") in the browser."""
        ...

    def fingerprint(self) -> str:
        """Changes whenever the screen or a field value changes; the loop's cheapest progress check."""
        ...
