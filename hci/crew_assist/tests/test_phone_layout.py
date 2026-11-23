"""The phone page keeps the map and buttons out from under the STOP bar."""
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = open(os.path.join(HERE, "..", "phone", "static", "style.css")).read()
JS = open(os.path.join(HERE, "..", "phone", "static", "app.js")).read()


def test_stop_bar_reserves_its_own_height_and_the_safe_area():
    assert "--stop-height: 76px" in CSS
    assert "--stop-gap: 12px" in CSS
    assert "env(safe-area-inset-bottom, 0px)" in CSS
    assert "padding: 12px 12px var(--stop-clearance)" in CSS
    assert "height: var(--stop-height)" in CSS
    assert "position: fixed" in CSS
    assert "bottom: calc(var(--stop-gap) + env(safe-area-inset-bottom, 0px))" in CSS


def test_confirm_shows_the_command_being_sent():
    assert 'statusEl.textContent = "Moving. " + pending' in JS
    assert "body.activity" in JS
    assert "activity.step" in JS
    assert 'pending && (body.busy || (body.last || "") === heldLast)' in JS
