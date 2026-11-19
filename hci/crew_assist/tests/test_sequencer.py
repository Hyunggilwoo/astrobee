import math
import os

import pytest

import astrobee_cmd as ac
from intent_parser import Intent, IntentParser, load_locations
from sequencer import Sequencer

LOCS = load_locations(os.path.join(os.path.dirname(__file__), "..", "locations.yaml"))


def make(tmp_path, **mock_kw):
    be = ac.MockBackend(**mock_kw)
    seq = Sequencer(ac.AstrobeeCommander(be), LOCS, image_dir=str(tmp_path))
    return be, seq


def names(be):
    return [c.cmd_name for c in be.sent]


# --- encoding matches simple_move.cc / teleop_tool.cc -------------------------
def test_simple_move_encoding():
    c = ac.build_simple_move([11.25, -6.59, 4.8], [0, 0, 0, 1])
    assert c.cmd_name == "simpleMove6DOF" and c.cmd_src == "guest_science"
    assert c.cmd_origin == ""  # "guest_science" origin is rejected without a primary APK
    assert [a.data_type for a in c.args] == [ac.DATA_TYPE_STRING, ac.DATA_TYPE_VEC3d,
                                             ac.DATA_TYPE_VEC3d, ac.DATA_TYPE_MAT33f]
    assert c.args[0].value == "world"
    assert c.args[1].value == [11.25, -6.59, 4.8]
    assert c.args[2].value == [0.0, 0.0, 0.0]
    assert c.args[3].value == [0, 0, 0, 1, 0, 0, 0, 0, 0]


def test_dock_and_camera_encoding():
    d = ac.build_dock(1)
    assert d.cmd_name == "dock" and d.args[0].data_type == ac.DATA_TYPE_INT and d.args[0].value == 1
    with pytest.raises(ValueError):
        ac.build_dock(3)
    s = ac.build_set_camera_streaming("Navigation", True)
    assert s.cmd_name == "setCameraStreaming"
    assert [(a.data_type, a.value) for a in s.args] == [(ac.DATA_TYPE_STRING, "Navigation"),
                                                         (ac.DATA_TYPE_BOOL, True)]


def test_unique_ids():
    ids = {ac.build_stop().cmd_id for _ in range(100)}
    assert len(ids) == 100


def test_axis_angle_matches_teleop_example():
    q = ac.axis_angle_to_quat(-3.14, (0, 0, 1))
    assert q[0] == 0 and q[1] == 0
    assert math.isclose(q[2], math.sin(-1.57)) and math.isclose(q[3], math.cos(-1.57))
    assert LOCS["dock_approach"]["quat"] == pytest.approx(list(q))


# --- sequencing -------------------------------------------------------------
def test_full_sortie(tmp_path):
    be, seq = make(tmp_path)
    assert seq.execute(Intent("undock")).ok
    assert seq.execute(Intent("go_to", "mid_jem")).ok
    r = seq.execute(Intent("return_to_dock"))
    assert r.ok, r.error
    assert names(be) == ["undock", "simpleMove6DOF", "simpleMove6DOF", "dock"]
    assert be.sent[2].args[1].value == LOCS["dock_approach"]["pos"]
    assert be.docked
    assert "autoReturn" not in names(be)


def test_dock_not_sent_if_approach_fails(tmp_path):
    be, seq = make(tmp_path, fail={"simpleMove6DOF": ac.EXEC_FAILED})
    be.docked = False
    r = seq.execute(Intent("return_to_dock"))
    assert not r.ok and "EXEC_FAILED" in r.error
    assert names(be) == ["simpleMove6DOF"]


@pytest.mark.parametrize("status", [ac.BAD_SYNTAX, ac.EXEC_FAILED, ac.CANCELED])
def test_bad_ack_aborts_without_stop(tmp_path, status):
    be, seq = make(tmp_path, fail={"undock": status})
    r = seq.execute(Intent("undock"))
    assert not r.ok and ac.COMPLETED_NAMES[status] in r.error
    assert names(be) == ["undock"]


def test_timeout_sends_stop_all_motion(tmp_path):
    be, seq = make(tmp_path, hang=["simpleMove6DOF"])
    be.docked = False
    r = seq.execute(Intent("go_to", "mid_jem"))
    assert not r.ok and "TIMEOUT" in r.error
    assert names(be) == ["simpleMove6DOF", "stopAllMotion"]


def test_return_to_dock_timeout_stops_and_skips_dock(tmp_path):
    be, seq = make(tmp_path, hang=["simpleMove6DOF"])
    be.docked = False
    seq.execute(Intent("return_to_dock"))
    assert names(be) == ["simpleMove6DOF", "stopAllMotion"]


def test_move_while_docked_fails_cleanly(tmp_path):
    be, seq = make(tmp_path)
    r = seq.execute(Intent("go_to", "mid_jem"))
    assert not r.ok and "docked" in r.error


def test_berth_is_never_a_move_target(tmp_path):
    be, seq = make(tmp_path)
    be.docked = False
    r = seq.execute(Intent("go_to", "berth1"))
    assert not r.ok and "return to dock" in r.error
    assert be.sent == []


def test_todo_pose_refused_by_default(tmp_path):
    locs = dict(LOCS, scratch={"pos": [11.0, -6.0, 4.8], "quat": [0, 0, 0, 1], "todo": True})
    be = ac.MockBackend()
    seq = Sequencer(ac.AstrobeeCommander(be), locs, image_dir=str(tmp_path))
    be.docked = False
    r = seq.execute(Intent("photograph", "scratch"))
    assert not r.ok and "TODO" in r.error and be.sent == []


def test_photograph_saves_png(tmp_path):
    be = ac.MockBackend()
    be.docked = False
    seq = Sequencer(ac.AstrobeeCommander(be), LOCS, image_dir=str(tmp_path))
    r = seq.execute(Intent("photograph", "rack_one"))
    assert r.ok, r.error
    assert names(be) == ["simpleMove6DOF", "setCameraStreaming"]
    with open(r.image_path, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"


def test_photograph_camera_enable_failure_is_not_fatal(tmp_path):
    be = ac.MockBackend(fail={"setCameraStreaming": ac.EXEC_FAILED})
    be.docked = False
    seq = Sequencer(ac.AstrobeeCommander(be), LOCS, image_dir=str(tmp_path))
    assert seq.execute(Intent("photograph", "rack_two")).ok


def test_stop_while_docked_reports_failure(tmp_path):
    be, seq = make(tmp_path)
    r = seq.execute(Intent("stop"))
    assert not r.ok and "docked" in r.error


def test_parser_to_sequencer_script(tmp_path):
    be, seq = make(tmp_path)
    p = IntentParser(LOCS)
    for line in ["Undock please", "fly to the middle of JEM", "return to dock"]:
        assert seq.execute(p.parse(line)).ok
    assert be.docked


def test_cli_mock_script(tmp_path, capsys):
    import cli
    rc = cli.main(["--mock", "--yes", "--image-dir", str(tmp_path),
                   "-c", "undock", "-c", "go to mid jem", "-c", "return to dock"])
    assert rc == 0
    rc = cli.main(["--mock", "--yes", "-c", "go to the cupola"])
    assert rc == 1
