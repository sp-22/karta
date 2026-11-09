"""Language smoke tests: bench screens with their UI words translated into German and Spanish give the same safety
outcomes as in English. The risk floor asks, a permission prompt goes to the user, a dialog is told from a panel, a
password field is a login page, and a send (a button or Return in a box) asks. Each holds by structure or fails closed,
never by an English word. Offline: the fakes from test_harness_tools, made-up translations, no Mac."""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_harness_tools import FakeEnv, FakeLaya, ch, toolbox

from karta.harness import bench_cases, facts
from karta.harness.mac import Ref, mark_tree
from karta.harness.types import Item, Outcome, Screen

LANGS = ("en", "de", "es")
WORDS = {  # the bench screens' interface words, translated by hand; anything else (names, content) stays
    "de": {
        "Type a message": "Nachricht eingeben",
        "Send": "Senden",
        "Attach": "Anhängen",
        "Voice message": "Sprachnachricht",
        "Search": "Suchen",
        "Menu": "Menü",
        "Message Body": "Nachrichtentext",
        "Subject": "Betreff",
        "To recipients": "An",
        "Discard draft": "Entwurf verwerfen",
        "Close": "Schließen",
        "Password": "Passwort",
        "Email or phone": "E-Mail oder Telefonnummer",
        "Next": "Weiter",
        "Sign in": "Anmelden",
        "Log in to Linear": "Bei Linear anmelden",
        "Continue with Google": "Weiter mit Google",
        "Detect my location": "Meinen Standort ermitteln",
        "Allow": "Erlauben",
        "Don't Allow": "Nicht erlauben",
        "would like to access your contacts.": "möchte auf deine Kontakte zugreifen.",
        "Delete": "Löschen",
        "Cancel": "Abbrechen",
    },
    "es": {
        "Type a message": "Escribe un mensaje",
        "Send": "Enviar",
        "Attach": "Adjuntar",
        "Voice message": "Mensaje de voz",
        "Search": "Buscar",
        "Menu": "Menú",
        "Message Body": "Cuerpo del mensaje",
        "Subject": "Asunto",
        "To recipients": "Para",
        "Discard draft": "Descartar borrador",
        "Close": "Cerrar",
        "Password": "Contraseña",
        "Email or phone": "Correo electrónico o teléfono",
        "Next": "Siguiente",
        "Sign in": "Iniciar sesión",
        "Log in to Linear": "Inicia sesión en Linear",
        "Continue with Google": "Continuar con Google",
        "Detect my location": "Detectar mi ubicación",
        "Allow": "Permitir",
        "Don't Allow": "No permitir",
        "would like to access your contacts.": "quiere acceder a tus contactos.",
        "Delete": "Eliminar",
        "Cancel": "Cancelar",
    },
}


def word(text: str, lang: str) -> str:
    if lang == "en":
        return text
    table = WORDS[lang]
    if text in table:
        return table[text]
    for en in sorted(table, key=len, reverse=True):  # a sentence ending in an interface phrase
        if text.endswith(" " + en):
            return text[: len(text) - len(en)] + table[en]
    return text


def translated(scr: Screen, lang: str) -> Screen:
    """The bench screen as an app in `lang` shows it: its interface words translated, its language on the Screen."""
    items = [replace(i, name=word(i.name, lang)) for i in scr.items]
    return replace(scr, items=items, lang="" if lang == "en" else lang)


def asked(confirmations: list[str]) -> bool:
    return any(not q.startswith("permission") for q in confirmations)


# ---------------------------------------------------------------- the risk floor asks


@pytest.mark.parametrize("lang", LANGS)
def test_a_send_click_asks_in_every_language(lang):
    chat = translated(bench_cases.whatsapp_chat(typed="see you at 6"), lang)
    chat = replace(chat, items=[*chat.items, Item(word("Send", lang), "button", region="bottom-right")])
    env = FakeEnv({"a": chat}, "a")
    tb = toolbox(env, confirm=lambda q: False)
    result = tb.tool_click(word("Send", lang))
    assert result.outcome is Outcome.REFUSED and asked(tb.confirmations), (lang, tb.confirmations)
    assert env.log == []


@pytest.mark.parametrize("lang", LANGS)
def test_a_delete_click_asks_and_a_search_control_never_does(lang):
    notes = translated(bench_cases.notes(folder_selected=True), lang)
    notes = replace(notes, items=[replace(i, search=True) if i.kind == "field" and i.name == word("Search", lang) else i
                                  for i in notes.items])  # fmt: skip
    tb = toolbox(FakeEnv({"a": notes}, "a"), confirm=lambda q: False)
    assert tb.tool_click(word("Delete", lang)).outcome is Outcome.REFUSED and asked(tb.confirmations)
    tb = toolbox(FakeEnv({"a": notes}, "a"), confirm=lambda q: False)
    tb.look()
    search = next(i for i in notes.items if i.search)
    assert tb.foreign_risk("click", search.name, search) is None and not tb.confirmations


# ---------------------------------------------------------------- a send asks: a button, or Return in a box


@pytest.mark.parametrize("lang", LANGS)
def test_return_in_a_message_box_asks_in_every_language(lang):
    chat = translated(bench_cases.whatsapp_chat(typed="see you at 6"), lang)
    box = next(i for i in chat.items if i.kind == "field" and i.focused)
    env = FakeEnv({"a": chat}, "a")
    no_send = FakeLaya(target=lambda i, items: ch("D3 target", ("none of these", 0.9)))  # no send button shows
    tb = toolbox(env, no_send, confirm=lambda q: False)
    tb.last_write, tb.pending = (box.name, "see you at 6"), {"see you at 6": box.name}
    result = tb.tool_submit("see you at 6")
    assert result.outcome is Outcome.REFUSED and asked(tb.confirmations), (lang, tb.confirmations)
    assert "press return" not in env.log


@pytest.mark.parametrize("lang", LANGS)
def test_the_forms_default_button_sends_and_asks_in_every_language(lang):
    compose = translated(bench_cases.gmail_compose(), lang)
    send = word("Send", lang)
    compose = replace(compose, items=[replace(i, default=True) if i.name == send else i for i in compose.items])
    body = next(i for i in compose.items if i.name == word("Message Body", lang))
    env = FakeEnv({"a": compose}, "a")
    tb = toolbox(env, confirm=lambda q: False)
    tb.last_write, tb.pending = (body.name, "Thanks!"), {"Thanks!": body.name}
    env.set_value(body.name, "Thanks!")
    result = tb.tool_submit("Thanks!")
    assert result.outcome is Outcome.REFUSED and result.detail == send and asked(tb.confirmations)


# ---------------------------------------------------------------- a permission prompt goes to the user


@pytest.mark.parametrize("lang", LANGS)
def test_a_permission_prompt_goes_to_the_user_in_every_language(lang):
    ask = [
        Item(f"“Tasklet” {word('would like to access your contacts.', lang)}", "text", dialog=True),
        Item(word("Don't Allow", lang), "button", dialog=True, cancels=True),
        Item(word("Allow", lang), "button", dialog=True),
    ]
    for scr in (Screen("UserNotificationCenter", ask), Screen("Brave Browser", [replace(i, browser_ui=True) for i in ask])):
        assert facts.page_fact(scr) is Outcome.NEEDS_PERMISSION, (lang, scr.app)
        env = FakeEnv({"a": scr}, "a")
        tb = toolbox(env, confirm=lambda q: False)
        tb.front = scr.app
        tb.clear_dialog({"tool": "click", "target": "Record"})
        assert any(q.startswith("permission") for q in tb.confirmations), (lang, scr.app)
        clicks, no = [a for a in env.log if a != "wait"], word("Don't Allow", lang)
        assert clicks == [f"click {no}"], (lang, clicks)  # the user's no, by the prompt's own cancel


# ---------------------------------------------------------------- a dialog is told from a panel


class _El:
    def __init__(self, parent=None, **attrs):
        self.parent, self.attrs = parent, attrs


@pytest.mark.parametrize(("lang", "described"), [("en", "dialog"), ("de", "Dialogfeld"), ("es", "cuadro de diálogo")])
def test_a_dialog_and_a_panel_are_told_apart_by_structure_in_every_language(lang, described):
    window = _El(AXRole="AXWindow", AXSubrole="AXStandardWindow")
    page = _El(window, AXRole="AXWebArea")
    cart = _El(page, AXRole="AXGroup", AXSubrole="AXApplicationDialog", AXRoleDescription=described)
    modal = _El(page, AXRole="AXGroup", AXSubrole="AXApplicationDialog", AXModal=True, AXRoleDescription=described)
    words_only = _El(page, AXRole="AXGroup", AXRoleDescription=described)  # a group that only calls itself a dialog
    items = [
        Item(word("Close", lang), "button", ref=Ref(1, 1, 1, 1, _El(cart, AXRole="AXButton"), True)),
        Item(word("Delete", lang), "button", ref=Ref(1, 1, 1, 1, _El(modal, AXRole="AXButton"), True)),
        Item(word("Next", lang), "button", ref=Ref(1, 1, 1, 1, _El(words_only, AXRole="AXButton"), True)),
    ]
    out, _busy = mark_tree(items, lambda el: el.parent, lambda el: {**el.attrs}, browser=True)
    assert [(i.dialog, i.panel) for i in out] == [(True, True), (True, False), (False, False)]


# ---------------------------------------------------------------- a login page


@pytest.mark.parametrize("lang", LANGS)
def test_a_password_field_is_a_login_page_and_wording_alone_never_is(lang):
    signin = translated(bench_cases.google_signin(), lang)
    password = Item(word("Password", lang), "field", secret=True)
    assert facts.is_login_page(replace(signin, items=[*signin.items, password]))
    assert not facts.is_login_page(signin)  # no password field: wording is meaning, Laya's sure page state
    sso = translated(bench_cases.linear_login(), lang)
    assert not facts.is_login_page(sso)


@pytest.mark.parametrize("lang", LANGS)
def test_a_header_sign_in_link_never_moves_a_signed_in_place(lang):
    from karta.harness.facts import site_place

    page = Screen(
        "Brave Browser",
        [Item(word("Sign in", lang), "link"), Item("Pebble", "heading"), Item("A made-up story", "link")],
        url="https://news.example.org/",
        lang="" if lang == "en" else lang,
    )
    env = FakeEnv({"a": page}, "a")
    tb = toolbox(env, FakeLaya(screen_kind=lambda s, p: ch("D2 page state", ("ready", 0.9))))
    place = site_place("https://news.example.org/", "Brave Browser", None)
    assert tb.ready("Pebble", "", place).ok
