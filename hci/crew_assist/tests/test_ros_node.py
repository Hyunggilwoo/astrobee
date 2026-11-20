import os
import time

import ros_node
from intent_parser import IntentParser, load_locations

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCS = load_locations(os.path.join(HERE, "locations.yaml"))


def test_web_button_phrases_match_the_parser():
    parser = IntentParser(LOCS)
    assert parser.parse("undock").action == "undock"
    assert parser.parse("go to rack alpha").target == "rack_one"
    assert parser.parse("go to rack bravo").target == "rack_two"
    assert parser.parse("go to rack charlie").target == "rack_three"
    assert parser.parse("return to dock").action == "return_to_dock"
    assert parser.parse("stop").action == "stop"


def test_bridge_stop_preempts_and_status_counts():
    session, bridge = ros_node.make_runtime(
        os.path.join(HERE, "locations.yaml"), True, False,
        os.path.join(HERE, "images"), 1.0, 5.0)
    session.cmd.backend.docked = False
    bridge.handle_text("go to rack alpha")
    backend = session.cmd.backend
    started = time.time()
    while not backend.moving.is_set():
        assert time.time() - started < 3.0
        time.sleep(0.01)
    started = time.time()
    bridge.handle_text("stop")
    session.wait_idle()
    assert time.time() - started < 1.0
    status = bridge.status_dict()
    assert status["busy"] is False
    assert status["result_count"] == 1
    assert status["last_ok"] is False
    assert "CANCELED" in status["last"]
    assert any("stopAllMotion" in line for line in status["lines"])
    session.close()


def test_mock_sortie_undock_rack_alpha_dock(tmp_path):
    rc = ros_node.main([
        "--mock", "--yes",
        "-c", "undock",
        "-c", "go to rack alpha",
        "-c", "return to dock",
        "--image-dir", str(tmp_path),
    ])
    assert rc == 0
