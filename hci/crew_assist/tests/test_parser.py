import os

import pytest

from intent_parser import IntentParser, ParseError, load_locations

LOCS = load_locations(os.path.join(os.path.dirname(__file__), "..", "locations.yaml"))


@pytest.fixture
def p():
    return IntentParser(LOCS)


@pytest.mark.parametrize("text,action,target", [
    ("undock", "undock", None),
    ("Astrobee, please undock.", "undock", None),
    ("leave the dock", "undock", None),
    ("go to mid JEM", "go_to", "mid_jem"),
    ("Fly to the middle of JEM", "go_to", "mid_jem"),
    ("move to Kibo please", "go_to", "mid_jem"),
    ("navigate to the dock approach", "go_to", "dock_approach"),
    ("photograph rack one", "photograph", "rack_one"),
    ("Take a picture of rack two", "photograph", "rack_two"),
    ("snap rack 3", "photograph", "rack_three"),
    ("take a photo of the far rack", "photograph", "rack_three"),
    ("go to the opposite rack", "go_to", "rack_two"),
    ("fly to rack charlie", "go_to", "rack_three"),
    ("return to dock", "return_to_dock", None),
    ("Return to the dock.", "return_to_dock", None),
    ("go home", "return_to_dock", None),
    ("go to the dock", "return_to_dock", None),   # never a plain move into the berth
    ("dock", "return_to_dock", None),
    ("STOP!", "stop", None),
    ("stop all motion", "stop", None),
    ("abort", "stop", None),
])
def test_known(p, text, action, target):
    i = p.parse(text)
    assert (i.action, i.target) == (action, target)


@pytest.mark.parametrize("text", [
    "", "dance", "open the hatch", "go to the cupola", "photograph this panel", "photograph panel a", "photograph rack four",
    "take a picture of that", "autoReturn", "go to", "self destruct",
])
def test_rejected(p, text):
    with pytest.raises(ParseError):
        p.parse(text)


def test_deictic_message_lists_places(p):
    with pytest.raises(ParseError, match="ambiguous"):
        p.parse("photograph this panel")


def test_locations_valid_unit_quats():
    import math
    for name, loc in LOCS.items():
        q = loc["quat"]
        assert abs(math.sqrt(sum(v * v for v in q)) - 1.0) < 1e-6, name
