"""Shared command fields, ack wait, and env defaults. No ROS."""
import astrobee_cmd as ac
import command_fields
import hci_config
from bridge_codec import arg_to_dict, command_to_dict, dict_to_command


def test_arg_dict_round_trip_matches_the_old_layout():
    cmd = ac.build_simple_move((1.0, 2.0, 3.0), (0.0, 0.0, 0.0, 1.0))
    again = dict_to_command(command_to_dict(cmd))
    assert again.cmd_name == cmd.cmd_name
    assert again.cmd_origin == ""
    assert again.args[1].value == [1.0, 2.0, 3.0]
    assert arg_to_dict(cmd.args[0])["s"] == "world"
    assert command_fields.DATA_TYPE_VEC3D == ac.DATA_TYPE_VEC3d


def test_wait_final_ack_times_out_and_abandon_matches_the_ros_paths():
    def poll():
        return None

    def pause(_seconds):
        return None

    timed = ac.wait_final_ack(
        "id", 0.0, poll, pause, is_stop=False, abort_event=None, slice_s=0.01
    )
    assert timed.completed == ac.TIMEOUT
    assert "no final ack" in timed.message

    class Flag:
        def is_set(self):
            return True

    abandoned = ac.wait_final_ack(
        "id",
        5.0,
        poll,
        pause,
        is_stop=False,
        abort_event=Flag(),
        abort_grace_s=-1.0,
        slice_s=0.01,
    )
    assert abandoned.completed == ac.CANCELED
    assert abandoned.message == "no ack after stop; abandoned locally"

    stop = ac.wait_final_ack(
        "id",
        0.0,
        poll,
        pause,
        is_stop=True,
        abort_event=Flag(),
        abort_grace_s=-1.0,
        slice_s=0.01,
    )
    assert stop.completed == ac.TIMEOUT


def test_env_defaults_match_compose():
    assert hci_config.web_port() == 8090
    assert hci_config.web_bind() == "0.0.0.0"
    assert hci_config.web_backend() == "ros1"
    assert hci_config.heartbeat_s() == 2.0
    assert hci_config.bridge_host() == "127.0.0.1"
    assert hci_config.bridge_port() == 11511
