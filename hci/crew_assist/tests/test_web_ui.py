import os

from smoke_mission import STEPS

WEB = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "docker", "web"))


def _read(name):
    with open(os.path.join(WEB, name)) as handle:
        return handle.read()


def test_page_exposes_the_sortie_buttons_and_stop():
    html = _read("index.html")
    for phrase in ("undock", "go to rack alpha", "go to rack bravo",
                   "go to rack charlie", "return to dock"):
        assert 'data-command="%s"' % phrase in html
        assert 'data-confirm="1"' in html
    assert 'id="stop"' in html
    assert 'data-command="stop"' in html
    assert "autoReturn" in html
    assert "cmd_origin" in html
    assert "/hw/cam_nav" in _read("app.js")
    assert "/mgt/ack" in _read("app.js")
    assert "/loc/pose" in _read("app.js")
    assert "/crew_assist/intent" in _read("app.js")
    assert "ROSLIB" in _read("vendor/roslib.min.js")
    assert STEPS == ["undock", "go to rack alpha", "return to dock"]
