def test_a_safe_word_inside_a_longer_label_never_exempts_it():
    from karta.harness.risk import risky

    assert risky("click", "Cancel") is None and risky("click", "Not now") is None
    assert risky("click", "Cancel order") == "cancel" and risky("click", "Cancel subscription") == "cancel"
    assert risky("click", "Preview and publish") == "post"


def test_return_in_a_box_with_no_real_name_asks_first():
    from karta.harness.risk import risky

    assert risky("submit", "") == "send" and risky("submit", "text field") == "send"
    assert risky("submit", "Search") is None
