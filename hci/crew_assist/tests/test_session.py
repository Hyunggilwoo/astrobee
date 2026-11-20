import os
import signal
import subprocess
import sys
import time

import astrobee_cmd as ac
from intent_parser import IntentParser, load_locations
from sequencer import Sequencer
from session import CrewSession

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCS = load_locations(os.path.join(HERE, "locations.yaml"))


def make(move_time=0.6):
    be = ac.MockBackend(move_time_s=move_time)
    cmd = ac.AstrobeeCommander(be)
    msgs = []
    s = CrewSession(IntentParser(LOCS), Sequencer(cmd, LOCS), cmd, echo=msgs.append)
    return be, cmd, s, msgs


def wait_moving(be, timeout=3.0):
    t = time.time()
    while not be.moving.is_set():
        assert time.time() - t < timeout, "mock never started moving"
        time.sleep(0.01)


def test_typed_stop_preempts_move():
    be, cmd, s, msgs = make(move_time=5.0)
    be.docked = False
    s.submit("go to mid jem")
    wait_moving(be)
    t0 = time.time()
    s.submit("stop")
    s.wait_idle()
    assert time.time() - t0 < 1.0  # did not wait 5 s for the move
    r = s.results[-1]
    assert not r.ok and r.steps[-1].status == "CANCELED"
    assert [c.cmd_name for c in be.sent] == ["simpleMove6DOF", "stopAllMotion"]
    stop_ack = [res for c, res in cmd.history if c.cmd_name == "stopAllMotion"][0]
    assert stop_ack.ok
    s.close()


def test_stop_drops_queued_and_blocks_next_step():
    be, cmd, s, msgs = make(move_time=5.0)
    be.docked = False
    s.submit("return to dock")  # approach then dock
    s.submit("go to mid jem")  # queued
    wait_moving(be)
    s.submit("stop")
    s.wait_idle()
    names = [c.cmd_name for c in be.sent]
    assert names == ["simpleMove6DOF", "stopAllMotion"]  # no dock, no queued move
    assert any("dropped 1 queued" in m for m in msgs)
    s.close()


def test_new_command_after_stop_runs():
    be, cmd, s, msgs = make(move_time=0.3)
    be.docked = False
    s.submit("go to mid jem")
    wait_moving(be)
    s.submit("stop")
    s.wait_idle()
    s.submit("return to dock")
    s.wait_idle()
    assert s.results[-1].ok and be.docked
    s.close()


def test_confirm_declined_sends_nothing():
    be = ac.MockBackend()
    cmd = ac.AstrobeeCommander(be)
    s = CrewSession(
        IntentParser(LOCS),
        Sequencer(cmd, LOCS),
        cmd,
        confirm=lambda i: False,
        echo=lambda m: None,
    )
    assert s.submit("undock") is None
    s.wait_idle()
    assert be.sent == []
    s.close()


def _spawn(*extra):
    return subprocess.Popen(
        [
            sys.executable,
            "-u",
            os.path.join(HERE, "cli.py"),
            "--mock",
            "--yes",
            "--mock-move-time",
            "5",
            *extra,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def test_cli_piped_typed_stop():
    p = _spawn()
    p.stdin.write("undock\n")
    p.stdin.flush()
    time.sleep(1.0)
    p.stdin.write("stop\n")
    p.stdin.flush()
    time.sleep(0.5)
    p.stdin.close()
    p.wait(timeout=20)
    out = p.stdout.read()
    assert "stop requested -> stopAllMotion" in out
    assert "undock:CANCELED" in out


def test_cli_sigint_stops_not_exits():
    p = _spawn()
    p.stdin.write("undock\n")
    p.stdin.flush()
    time.sleep(1.0)
    p.send_signal(signal.SIGINT)
    time.sleep(0.5)
    assert p.poll() is None  # Ctrl-C did not kill the CLI
    p.stdin.write("quit\n")
    p.stdin.flush()
    out = p.communicate(timeout=20)[0]
    assert "Ctrl-C -> stopAllMotion" in out
    assert "undock:CANCELED" in out
