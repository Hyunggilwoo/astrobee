import os

import pytest
import voice_input as vi
from intent_parser import IntentParser, load_locations

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCS = load_locations(os.path.join(HERE, "locations.yaml"))
MODEL = vi.DEFAULT_MODEL
VT = os.path.join(HERE, "..", "voice_test")


def test_speakable_digits():
    assert vi.speakable("rack 1") == "rack one"
    assert vi.speakable("Panel-2") == "panel two"


def test_grammar_contents():
    phrases, dropped = vi.build_grammar(LOCS)
    assert dropped == []
    assert "photograph rack bravo" in phrases and "go to mid jem" in phrases
    assert "return to dock" in phrases and "stop" in phrases
    assert "dock" not in phrases  # stop/dock confusion guard
    assert not any("berth" in p for p in phrases)  # berths only via return to dock
    assert not any("kibo" in p for p in phrases)  # typed-only alias
    assert "go to the cupola" in phrases  # decoy present


def test_every_voice_phrase_parses_or_is_decoy():
    p = IntentParser(LOCS)
    phrases, _ = vi.build_grammar(LOCS)
    vr = FakeVR()
    decoys = set(vi.decoy_phrases(LOCS))
    for ph in phrases:
        if ph in decoys:
            assert not vr.accept(vi.Heard(ph, 1.0, False, 1.0))[0], ph  # gate rejects
        else:
            p.parse(ph)  # must not raise


def test_racks_are_spoken_as_nato_names():
    phrases, _ = vi.build_grammar(LOCS)
    vr = FakeVR()
    assert (
        "photograph rack alpha" in vr.phrase_set
        and "fly to rack charlie" in vr.phrase_set
    )
    ok, why = vr.accept(vi.Heard("fly to rack three", 1.0, False, 1.0))
    assert not ok and "alpha / bravo / charlie" in why
    assert (
        IntentParser(LOCS).parse("fly to rack three").target == "rack_three"
    )  # typed still works
    assert IntentParser(LOCS).parse("photograph rack bravo").target == "rack_two"


def test_every_typed_rack_alias_is_a_voice_decoy():
    # 'opposite rack' was snapped to 'alpha rack' by Vosk when it was absent from
    # the grammar (188/527 synthetic clips -> wrong rack). Now it is a decoy.
    vr = FakeVR()
    for t in ["opposite rack", "far rack", "first rack", "rack two"]:
        assert t in vi.typed_only_targets(LOCS)
        ok, why = vr.accept(vi.Heard("take a picture of the %s" % t, 1.0, False, 1.0))
        assert not ok and "alpha / bravo / charlie" in why, t
    assert "alpha rack" not in vi.typed_only_targets(LOCS)


class FakeVR(vi.VoiceRecognizer):
    def __init__(self):  # no model needed for gate tests
        self.phrases, _ = vi.build_grammar(LOCS)
        self.decoy_set = set(vi.decoy_phrases(LOCS))
        self.phrase_set = set(self.phrases) - self.decoy_set
        self.min_conf = 0.6


@pytest.mark.parametrize(
    "heard,ok",
    [
        (vi.Heard("photograph rack alpha", 0.95, False), True),
        (vi.Heard("photograph rack alpha", 0.40, False), False),  # low confidence
        (
            vi.Heard("photograph rack one", 0.99, False),
            False,
        ),  # numbered rack = decoy now
        (vi.Heard("the dock", 0.90, False), False),  # fragment, not a phrase
        (vi.Heard("dock", 0.90, False), False),
        (vi.Heard("go to rack bravo", 0.90, True), False),  # [unk] noise around it
        (vi.Heard("stop", 0.20, True), True),  # stop is fail-safe
        (vi.Heard("abort", 0.30, False), True),
        (vi.Heard("", 0.0, False), False),
    ],
)
def test_gate(heard, ok):
    assert FakeVR().accept(heard)[0] is ok


def clip_for(voice, text):
    import json

    for m in json.load(open(os.path.join(VT, "manifest.json"))):
        if (
            m["voice"].startswith("en_")
            and voice in m["voice"]
            and m["text"] == text
            and m["cond"] == "clean"
        ):
            return os.path.join(VT, m["wav"])
    raise KeyError(text)


needs_model = pytest.mark.skipif(
    not (os.path.isdir(MODEL) and os.path.exists(os.path.join(VT, "manifest.json"))),
    reason="Vosk model or generated voice_test clips not present",
)


@needs_model
@pytest.mark.parametrize(
    "voice,text,expect",
    [
        ("lessac", "photograph rack alpha", ("photograph", "rack_one")),
        ("alan", "fly to rack bravo", ("go_to", "rack_two")),
        ("ryan", "photograph rack one", None),  # numbered racks: typed-only
        ("alan", "return to dock", ("return_to_dock", None)),
        ("ryan", "stop", ("stop", None)),
        ("lessac", "go to the cupola", None),  # must be rejected
        ("ryan", "move to kibo", None),  # typed-only alias
    ],
)
def test_real_clips(voice, text, expect):
    vr = vi.VoiceRecognizer(MODEL, LOCS)
    h = vr.recognize_wav(clip_for(voice, text))
    ok, why = vr.accept(h)
    if expect is None:
        assert not ok, (h.text, why)
    else:
        assert ok, why
        i = IntentParser(LOCS).parse(h.text)
        assert (i.action, i.target) == expect


@needs_model
def test_voice_cli_mock_mission(capsys):
    rc = vi.main(
        [
            "--wav",
            clip_for("lessac", "undock"),
            clip_for("lessac", "photograph rack alpha"),
            clip_for("lessac", "return to dock"),
            "--execute",
            "--mock",
            "--yes",
            "--image-dir",
            "/tmp/crew_voice_test",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "undock:OK" in out and "grab_image:OK" in out and "dock:OK" in out


@needs_model
def test_8khz_phone_audio_is_upsampled(tmp_path):
    import wave

    import numpy as np

    src = clip_for("lessac", "photograph rack alpha")
    with wave.open(src) as w:
        sr, x = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), "<i2")
    y = np.interp(np.arange(0, len(x), sr / 8000.0), np.arange(len(x)), x).astype("<i2")
    out = str(tmp_path / "phone8k.wav")
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(y.tobytes())
    vr = vi.VoiceRecognizer(MODEL, LOCS)
    h = vr.recognize_wav(out)  # used to raise "Failed to process waveform"
    assert vr.accept(h)[0] and IntentParser(LOCS).parse(h.text).target == "rack_one"


def test_mic_execute_refuses_yes():
    with pytest.raises(SystemExit):
        vi.main(["--mic", "--execute", "--mock", "--yes"])


def test_start_is_not_a_decoy():
    phrases, _ = vi.build_grammar(LOCS)
    assert "start" not in phrases  # it captured spoken "stop" in the broad test


# ---- push-to-talk -------------------------------------------------------------
def test_ptt_loop_consumes_one_wav_per_press(tmp_path):
    import wave

    paths = []
    for k in range(2):
        pth = str(tmp_path / ("c%d.wav" % k))
        with wave.open(pth, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 1600)
        paths.append(pth)

    class StubVR:
        def recognize_pcm(self, pcm, rate):
            return vi.Heard("len%d@%d" % (len(pcm), rate), 1.0, False, 1.0)

    lines = iter(["\n", "\n", "\n", "\n", "\n", "\n"])  # 3rd press: no audio left
    got = []
    n = vi.ptt_loop(
        StubVR(),
        vi.WavPTTSource(paths),
        lambda h, lab: got.append((h.text, lab)),
        readline=lambda: next(lines, ""),
        echo=lambda m: None,
    )
    assert n == 2 and got[0] == ("len3200@16000", paths[0])


def test_ptt_quit_and_eof():
    calls = []
    assert (
        vi.ptt_loop(
            None,
            vi.WavPTTSource([]),
            lambda h, l: calls.append(h),
            readline=iter(["q\n"]).__next__,
            echo=lambda m: None,
        )
        == 0
    )
    assert (
        vi.ptt_loop(
            None,
            vi.WavPTTSource([]),
            lambda h, l: calls.append(h),
            readline=lambda: "",
            echo=lambda m: None,
        )
        == 0
    )
    assert calls == []


def test_mic_ptt_execute_refuses_yes():
    with pytest.raises(SystemExit):
        vi.main(["--mic", "--ptt", "--execute", "--mock", "--yes"])


@needs_model
def test_ptt_cli_wav_mock(monkeypatch, capsys):
    import io

    monkeypatch.setattr(
        "sys.stdin", io.StringIO("\n\ny\nq\n")
    )  # talk, send, confirm, quit
    rc = vi.main(
        ["--wav", clip_for("lessac", "undock"), "--ptt", "--execute", "--mock"]
    )
    out = capsys.readouterr().out
    assert rc == 0 and "heard 'undock'" in out and "undock:OK" in out
    assert "EMERGENCY STOP" in out  # the prompt teaches the recommended stop phrase
