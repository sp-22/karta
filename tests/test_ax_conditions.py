"""The screen reader under conditions that broke it live, on synthetic accessibility trees (as tests/test_ax.py).

What a read walks is `karta.harness.mac.walk_app`: the app's focused window (and in full screen the strip windows it
is made of), its dialogs and panels and its menu bar, never its other windows; and a control is on screen only
inside the page or list that shows it (`ax_walk`'s clip by AXScrollArea and AXWebArea), not merely on the display.
"""

from __future__ import annotations

from karta.base.ax_walk import AxAttrs, front_windows, walk_actionable
from karta.harness.mac import walk_app

DISPLAY = (1728.0, 1117.0)


def node(role, label="", frame=(10.0, 10.0, 100.0, 20.0), children=(), press=False, subrole=None):
    return {"role": role, "label": label, "frame": frame, "children": list(children), "press": press, "subrole": subrole}


def window(frame, *children, subrole="AXStandardWindow"):
    return node("AXWindow", "", frame, children, subrole=subrole)


def app(*children):
    return node("AXApplication", "Brave Browser", (0.0, 1117.0, 0.0, 0.0), children)


def attrs(n):
    return AxAttrs(n["role"], n["label"], n["frame"])


def read(root, focused, display=DISPLAY):
    """(labels on screen, labels off screen but pressable) of a read of `root` with `focused` in front."""
    found, hidden = walk_app(
        root,
        focused,
        lambda n: n["children"],
        attrs,
        lambda n: ["AXPress"] if n["press"] else [],
        lambda n: n["subrole"],
        *display,
    )
    return [n.label for n in found], [n.label for n in hidden]


def web_page(frame, *children):
    """A browser page: nameless groups framed like the page, a scroll area, the web area, the content."""
    web = node("AXWebArea", "", frame, [node("AXGroup", "", frame, children)])
    return node("AXGroup", "", frame, [node("AXGroup", "", frame, [node("AXScrollArea", "", frame, [web])])])


# ---------------------------------------------------------------- full screen


def full_screen():
    page_frame = (0.0, 114.0, 1728.0, 1003.0)
    tabs = window((0.0, 0.0, 1728.0, 40.0), node("AXTab", "Recipes", (8.0, 6.0, 220.0, 28.0)))
    toolbar = window((0.0, 40.0, 1728.0, 74.0), node("AXTextField", "Address and search bar", (300.0, 52.0, 1000.0, 30.0)))
    page = window(
        page_frame,
        web_page(
            page_frame,
            node("AXTextField", "Search recipes", (500.0, 140.0, 600.0, 36.0)),
            node("AXLink", "Lemon rice", (100.0, 300.0, 200.0, 24.0)),
        ),
    )
    return app(tabs, toolbar, page), tabs, toolbar, page


def test_full_screen_reads_the_page_and_its_tab_bar_and_toolbar_windows():
    root, _tabs, _toolbar, page = full_screen()
    on, _off = read(root, page)
    assert on == ["Recipes", "Address and search bar", "Search recipes", "Lemon rice"]


def test_full_screen_with_the_toolbar_focused_still_reads_the_page():
    """Clicking the address bar can make the toolbar strip the focused window: the page below is the same window."""
    root, _tabs, toolbar, _page = full_screen()
    assert read(root, toolbar)[0] == ["Recipes", "Address and search bar", "Search recipes", "Lemon rice"]


# ---------------------------------------------------------------- split view and several windows


def test_split_view_reads_only_the_focused_half():
    left = window(
        (0.0, 0.0, 864.0, 1117.0), web_page((0.0, 80.0, 864.0, 1037.0), node("AXLink", "Left story", (40.0, 200.0, 200.0, 24.0)))
    )
    right = window(
        (864.0, 0.0, 864.0, 1117.0),
        web_page((864.0, 80.0, 864.0, 1037.0), node("AXLink", "Right story", (900.0, 200.0, 200.0, 24.0))),
    )
    root = app(left, right)
    assert read(root, right)[0] == ["Right story"]
    assert read(root, left)[0] == ["Left story"]


def test_only_the_focused_window_of_several_is_read_but_its_dialogs_and_panels_are():
    front = window((100.0, 100.0, 900.0, 700.0), node("AXButton", "New Note", (120.0, 120.0, 80.0, 24.0)))
    behind = window((60.0, 60.0, 900.0, 700.0), node("AXButton", "Old note toolbar", (80.0, 80.0, 120.0, 24.0)))
    far = window((1000.0, 300.0, 600.0, 500.0), node("AXButton", "Another note", (1020.0, 320.0, 120.0, 24.0)))
    dialog = window((400.0, 300.0, 400.0, 200.0), node("AXButton", "Don't Save", (420.0, 450.0, 100.0, 24.0)), subrole="AXDialog")
    panel = window(
        (1300.0, 100.0, 300.0, 400.0), node("AXButton", "Bold", (1310.0, 120.0, 40.0, 24.0)), subrole="AXFloatingWindow"
    )
    menu = node("AXMenuBar", "", (0.0, 0.0, 1728.0, 24.0), [node("AXMenuBarItem", "File", (40.0, 0.0, 30.0, 24.0))])
    root = app(behind, front, far, dialog, panel, menu)
    assert read(root, front)[0] == ["New Note", "Don't Save", "Bold", "File"]


def test_without_a_focused_window_every_window_is_read_as_before():
    one = window((0.0, 0.0, 400.0, 400.0), node("AXButton", "One", (10.0, 10.0, 60.0, 24.0)))
    two = window((500.0, 0.0, 400.0, 400.0), node("AXButton", "Two", (510.0, 10.0, 60.0, 24.0)))
    assert read(app(one, two), None)[0] == ["One", "Two"]
    stray = window((0.0, 0.0, 10.0, 10.0))  # a focused window that is not among the app's children
    assert read(app(one, two), stray)[0] == ["One", "Two"]


def test_a_window_whose_kind_is_unknown_is_kept():
    front = window((0.0, 0.0, 400.0, 400.0), node("AXButton", "One", (10.0, 10.0, 60.0, 24.0)))
    unknown = window((500.0, 0.0, 400.0, 400.0), node("AXButton", "Popup", (510.0, 10.0, 60.0, 24.0)), subrole=None)
    assert read(app(front, unknown), front)[0] == ["One", "Popup"]


def test_front_windows_keeps_the_strips_of_the_focused_window_only():
    page = window((0.0, 114.0, 1728.0, 1003.0))
    strip = window((0.0, 40.0, 1728.0, 74.0))
    narrow = window((0.0, 60.0, 400.0, 54.0))  # short and touching, but not across the page: no strip of it
    beside = window((1728.0, 114.0, 600.0, 120.0))
    kept = front_windows([strip, narrow, page, beside], page, attrs, lambda n: n["subrole"])
    assert kept == [strip, page]


# ---------------------------------------------------------------- a small window, a scrolled page


def test_a_small_window_s_page_below_its_edge_is_not_on_screen():
    """The display goes on below a small window: what the page has past its bottom edge shows nothing there (another
    app is), so it is no item; a pressable one stays reachable off screen."""
    page_frame = (200.0, 280.0, 500.0, 300.0)  # the web area: 500 x 300 pt
    root = app(
        window(
            (200.0, 200.0, 500.0, 380.0),
            web_page(
                page_frame,
                node("AXLink", "Visible link", (220.0, 300.0, 200.0, 24.0), press=True),
                node("AXLink", "Below the window", (220.0, 700.0, 200.0, 24.0), press=True),
                node("AXButton", "Right of the window", (760.0, 400.0, 100.0, 24.0), press=True),
            ),
        )
    )
    on, off = read(root, root["children"][0])
    assert on == ["Visible link"]
    assert off == ["Below the window", "Right of the window"]


def test_a_scrolled_page_keeps_what_shows_and_not_what_scrolled_out():
    page_frame = (0.0, 114.0, 1440.0, 786.0)
    root = app(
        window(
            (0.0, 25.0, 1440.0, 875.0),
            node("AXTextField", "Address and search bar", (300.0, 60.0, 800.0, 30.0)),
            web_page(
                page_frame,
                node("AXLink", "Scrolled under the toolbar", (100.0, 70.0, 200.0, 24.0), press=True),  # on the display
                node("AXHeading", "Half above the top", (100.0, 100.0, 300.0, 40.0), press=True),  # centre 120: shows
                node("AXLink", "In view", (100.0, 400.0, 200.0, 24.0)),
                node("AXLink", "Half below the bottom", (100.0, 890.0, 200.0, 30.0), press=True),  # centre 905: does not
                node("AXLink", "Far below", (100.0, 4000.0, 200.0, 24.0), press=True),
            ),
        )
    )
    on, off = read(root, root["children"][0])
    assert on == ["Address and search bar", "Half above the top", "In view"]
    assert off == ["Scrolled under the toolbar", "Half below the bottom", "Far below"]


def test_a_scrolled_list_inside_a_window_clips_its_rows():
    sidebar = node(
        "AXScrollArea",
        "",
        (0.0, 50.0, 250.0, 400.0),
        [
            node("AXRow", "Projects", (0.0, 60.0, 250.0, 30.0)),
            node("AXRow", "Archive 2019", (0.0, 600.0, 250.0, 30.0), press=True),  # on the display, below the list
        ],
    )
    root = app(window((0.0, 25.0, 900.0, 700.0), sidebar, node("AXButton", "Below the list", (10.0, 600.0, 120.0, 24.0))))
    on, off = read(root, root["children"][0])
    assert on == ["Below the list", "Projects"] and off == ["Archive 2019"]


def test_a_popover_past_its_window_s_edge_is_read():
    popover = node(
        "AXPopover", "", (600.0, 100.0, 300.0, 200.0), [node("AXButton", "Pick a colour", (620.0, 120.0, 120.0, 24.0))]
    )
    pane = node("AXScrollArea", "", (0.0, 50.0, 500.0, 400.0), [popover])
    root = app(window((0.0, 25.0, 500.0, 500.0), pane))
    assert read(root, root["children"][0])[0] == ["Pick a colour"]


# ---------------------------------------------------------------- nested groups of one frame


def test_nested_same_frame_groups_at_several_levels_are_walked_through():
    frame = (0.0, 114.0, 1440.0, 786.0)
    deepest = node("AXButton", "Add to cart", (600.0, 500.0, 120.0, 32.0))
    labelled = node("AXGroup", "Product grid", frame, [node("AXGroup", "", frame, [node("AXGroup", "", frame, [deepest])])])
    overlay = node("AXGroup", "", frame, [node("AXButton", "Accept cookies", (600.0, 820.0, 160.0, 32.0))])
    layer = node("AXGroup", "", frame, [node("AXGroup", "", frame, [labelled])])
    root = app(window((0.0, 25.0, 1440.0, 875.0), node("AXGroup", "", frame, [layer, overlay])))
    on, _off = read(root, root["children"][0])
    assert on == ["Accept cookies", "Add to cart"]  # the labelled group is a container, not a pressable item


def test_a_control_listed_under_two_same_frame_layers_is_one_item():
    frame = (0.0, 114.0, 1440.0, 786.0)
    root = app(
        window(
            (0.0, 25.0, 1440.0, 875.0),
            node("AXGroup", "", frame, [node("AXButton", "Checkout", (600.0, 500.0, 120.0, 32.0))]),
            node("AXGroup", "", frame, [node("AXButton", "Checkout", (600.0, 500.0, 120.0, 32.0))]),
        )
    )
    assert read(root, root["children"][0])[0] == ["Checkout"]


# ---------------------------------------------------------------- no windows


def test_an_app_with_no_windows_reads_its_menu_bar_only():
    menu = node("AXMenuBar", "", (0.0, 0.0, 1728.0, 24.0), [node("AXMenuBarItem", "File", (40.0, 0.0, 30.0, 24.0))])
    assert read(app(menu), None) == (["File"], [])
    assert read(app(), None) == ([], [])


def test_the_walk_alone_still_reads_every_window():
    """walk_actionable has no notion of focus; the front-window rule lives in what it is given to walk."""
    one = window((0.0, 0.0, 400.0, 400.0), node("AXButton", "One", (10.0, 10.0, 60.0, 24.0)))
    two = window((500.0, 0.0, 400.0, 400.0), node("AXButton", "Two", (510.0, 10.0, 60.0, 24.0)))
    found, _hidden, _capped = walk_actionable(app(one, two), lambda n: n["children"], attrs, lambda n: [], *DISPLAY)
    assert [n.label for n in found] == ["One", "Two"]
