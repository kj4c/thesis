# the planner's structured output: junk placeholders from the model must never
# crash parsing, and every decision must map to a real browser action (or a
# clean failed `done`).

import pytest

from nodes import PlanDecision, _decision_to_action


def _d(**kw):
    return PlanDecision(reasoning="r", **kw)


@pytest.mark.parametrize("junk", ["", "<UNKNOWN>", "null", "N/A", "abc", None, True])
def test_junk_index_becomes_none(junk):
    assert _d(action="click", index=junk).index is None


def test_numeric_string_index_is_parsed():
    assert _d(action="click", index="17").index == 17


@pytest.mark.parametrize("raw,expected", [
    (True, True), ("true", True), ("down", True), ("1", True),
    (False, False), ("false", False), ("up", False),
    ("<UNKNOWN>", None), ("", None),
])
def test_scroll_direction_parsing(raw, expected):
    assert _d(action="scroll", down=raw).down is expected


@pytest.mark.parametrize("junk", ["", "null", "None", "N/A", "<unknown>", "undefined"])
def test_junk_strings_become_none(junk):
    d = _d(action="navigate", url=junk, text=junk)
    assert d.url is None and d.text is None


@pytest.mark.parametrize("raw,expected", [
    (False, False), ("false", False), ("no", False),
    (True, True), ("", True), ("<UNKNOWN>", True),
])
def test_success_flag_defaults_to_true_unless_clearly_false(raw, expected):
    assert _d(action="done", success=raw).success is expected


def test_unknown_action_is_rejected():
    with pytest.raises(ValueError):
        _d(action="teleport")


@pytest.mark.parametrize("decision,expected", [
    (dict(action="navigate", url="https://www.google.com"),
     {"navigate": {"url": "https://www.google.com", "new_tab": False}}),
    (dict(action="click", index=5), {"click": {"index": 5}}),
    (dict(action="input", index=2, text="spoons"), {"input": {"index": 2, "text": "spoons"}}),
    (dict(action="scroll"), {"scroll": {"down": True}}),
    (dict(action="scroll", down=False), {"scroll": {"down": False}}),
    (dict(action="extract", text="cheapest price"), {"extract": {"query": "cheapest price"}}),
    (dict(action="done", text="$4.99"), {"done": {"text": "$4.99", "success": True}}),
    (dict(action="done", text="couldn't find it", success=False),
     {"done": {"text": "couldn't find it", "success": False}}),
])
def test_decisions_map_to_browser_actions(tools, decision, expected):
    action = _decision_to_action(tools, _d(**decision))
    dump = action.model_dump(exclude_unset=True)
    name = next(iter(expected))
    for k, v in expected[name].items():
        assert dump[name][k] == v


@pytest.mark.parametrize("decision", [
    dict(action="click"),                 # no index
    dict(action="input", index=3),        # no text
    dict(action="navigate"),              # no url
    dict(action="extract"),               # no query
])
def test_incomplete_decisions_stop_as_failure(tools, decision):
    dump = _decision_to_action(tools, _d(**decision)).model_dump(exclude_unset=True)
    assert dump["done"]["success"] is False
    assert "incomplete action" in dump["done"]["text"]
