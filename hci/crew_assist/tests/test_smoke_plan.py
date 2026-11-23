"""Sortie steps follow the dock state. No ROS master required."""
import pytest
from smoke_mission import DOCKED, UNDOCKED, docked_from_state, sortie_steps


def test_a_docked_robot_undocks_before_the_rack():
    assert sortie_steps(True) == ["undock", "go to rack alpha", "return to dock"]
    assert docked_from_state(DOCKED) is True


def test_an_undocked_robot_skips_undock():
    assert sortie_steps(False) == ["go to rack alpha", "return to dock"]
    assert docked_from_state(UNDOCKED) is False


def test_an_in_progress_dock_state_is_not_a_start_state():
    assert docked_from_state(None) is None
    assert docked_from_state(10) is None  # UNKNOWN
    assert docked_from_state(1) is None  # still docking
    with pytest.raises(RuntimeError):
        sortie_steps(None)
