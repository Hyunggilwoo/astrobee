#!/usr/bin/env python3
"""Offline speech -> same IntentParser/CrewSession as the typed CLI.

Vosk (vosk-model-small-en-us-0.15) is run with a closed GRAMMAR built from the
command templates and the speakable aliases in locations.yaml, plus "[unk]".
Anything outside the grammar comes back as [unk] (or low confidence) and is
rejected, never guessed. Motion still needs a typed y/N confirmation unless
--yes. A recognised "stop" is sent immediately.

  # recognise WAV files only (no robot):
  python3 voice_input.py --wav clips/*.wav
  # mic on your own machine (pip install vosk sounddevice), drive the mock robot:
  python3 voice_input.py --mic --execute --mock
  # drive the sim (sourced Astrobee workspace):
  python3 voice_input.py --mic --execute
  # push-to-talk: Enter starts capture, Enter again sends it (q = quit):
  python3 voice_input.py --mic --ptt --execute
  # same PTT flow, but each Enter-pair consumes the next WAV (testing):
  python3 voice_input.py --wav a.wav b.wav --ptt --execute --mock

To stop the robot by voice, say "EMERGENCY STOP" (97 % recall in the broad
synthetic test vs 65 % for a bare "stop"; "stop"/"abort" are still accepted).
Ctrl-C or a typed `stop` in cli.py remain the primary stop.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import wave
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from intent_parser import IntentParser, ParseError, load_locations, normalize  # noqa: E402

DEFAULT_MODEL = os.path.join(HERE, "..", "models", "vosk-model-small-en-us-0.15")
log = logging.getLogger("crew_assist.voice")

FIXED_PHRASES = [
    "stop", "stop all motion", "abort", "halt", "freeze", "emergency stop",
    "undock", "leave the dock",
    "return to dock", "return to the dock", "go home", "go back to the dock",
    # NOTE: bare "dock" is deliberately NOT in the voice grammar: in noise Vosk
    # confused "stop" -> "dock", which would have started a return-to-dock.
]
STOP_WORDS = ("stop", "stop all motion", "abort", "halt", "freeze", "emergency stop")
# Decoys: real ISS places/things we do NOT fly to. With a closed grammar, an
# unknown name is otherwise forced onto the nearest known one (measured:
# "go to the cupola" -> "go to the kibo", conf 1.00). Decoys absorb those and
# the parser then rejects them as unknown locations.
DECOY_TARGETS = ["cupola", "node one", "node two", "node three", "harmony", "unity",
                 "tranquility", "columbus", "destiny", "the lab", "airlock", "hatch",
                 "window", "rack four", "rack five", "panel", "this panel", "that panel",
                 # old numbered rack names: recognised, then REJECTED with a hint,
                 # instead of being forced onto alpha/bravo/charlie
                 "rack one", "rack two", "rack three", "first rack", "second rack", "third rack"]
# Bare "this/that/here/there" decoys were removed: on real speech (Speech
# Commands) the decoy "that" captured 66/400 spoken "stop"s at 10 dB SNR.
DECOY_PHRASES = ["open the hatch", "close the hatch", "turn on the lights", "turn off the lights",
                 "what time is it", "take off", "land", "go"]
# "start" was removed from the decoys: in the broad synthetic test (31 voices,
# 10,540 clips) it captured 38 spoken "stop"s. A missed stop is worse than a
# false one, so nothing that sounds like "stop" may be a decoy.
GOTO_TEMPLATES = ["go to {}", "fly to {}", "move to {}", "go to the {}", "fly to the {}"]
PHOTO_TEMPLATES = ["photograph {}", "photograph the {}", "take a picture of {}",
                   "take a picture of the {}", "take a photo of the {}"]

_DIGITS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
           "6": "six", "7": "seven", "8": "eight", "9": "nine"}


def speakable(alias: str) -> str:
    """'panel 1' -> 'panel one' (Vosk emits words, never digits)."""
    t = normalize(alias)
    t = re.sub(r"\d", lambda m: " %s " % _DIGITS[m.group(0)], t)
    return re.sub(r"\s+", " ", t).strip()


# Typed-only aliases: measured as confusable in speech ("cupola" -> "kibo" at
# conf 0.96-1.00 with 10 dB noise), so they are not offered to the recogniser.
VOICE_EXCLUDED_ALIASES = {"kibo"}


def spoken_targets(locations: Dict[str, dict]) -> List[str]:
    out = []
    for name, loc in locations.items():
        if not loc.get("goto_allowed", True) or loc.get("todo") or "berth" in loc:
            continue  # berths/approach are reached via "return to dock"
        names = loc.get("spoken") or ([name.replace("_", " ")] + list(loc.get("aliases", [])))
        for a in names:
            s = speakable(a)
            s = re.sub(r"^the ", "", s)
            if s and s not in out and s not in VOICE_EXCLUDED_ALIASES:
                out.append(s)
    return out


def typed_only_targets(locations: Dict[str, dict]) -> List[str]:
    """Typed names of locations that have `spoken` names (e.g. 'opposite rack').
    They go into the grammar as decoys: if left out, Vosk snaps them onto the
    nearest real phrase ('opposite rack' -> 'alpha rack' 188/527 synthetic clips)."""
    spoken = set(spoken_targets(locations))
    out = []
    for name, loc in locations.items():
        if not loc.get("spoken"):
            continue
        for a in [name.replace("_", " ")] + list(loc.get("aliases", [])):
            t = re.sub(r"^the ", "", speakable(a))
            if t and t not in spoken and t not in out:
                out.append(t)
    return out


def all_decoy_targets(locations: Dict[str, dict]) -> List[str]:
    return list(dict.fromkeys(DECOY_TARGETS + typed_only_targets(locations)))


def build_grammar(locations: Dict[str, dict], in_vocab=None,
                  decoys: bool = True) -> Tuple[List[str], List[str]]:
    """Return (phrases, dropped). `in_vocab(word)->bool` filters OOV phrases."""
    phrases = list(FIXED_PHRASES)
    targets = spoken_targets(locations)
    for t in targets + (all_decoy_targets(locations) if decoys else []):
        phrases += [g.format(t) for g in GOTO_TEMPLATES]
        phrases += [p.format(t) for p in PHOTO_TEMPLATES]
    if decoys:
        phrases += DECOY_PHRASES
    seen, keep, dropped = set(), [], []
    for p in phrases:
        if p in seen:
            continue
        seen.add(p)
        if in_vocab is not None and not all(in_vocab(w) for w in p.split()):
            dropped.append(p)
        else:
            keep.append(p)
    return keep, dropped


def decoy_phrases(locations: Dict[str, dict]) -> List[str]:
    """Every grammar phrase that exists only to be rejected."""
    out = list(DECOY_PHRASES)
    for t in all_decoy_targets(locations):
        out += [g.format(t) for g in GOTO_TEMPLATES] + [p.format(t) for p in PHOTO_TEMPLATES]
    return out


@dataclass
class Heard:
    text: str
    confidence: float     # mean word confidence (0..1); 0 if nothing heard
    unknown: bool         # grammar fell back to [unk]
    min_word_conf: float = 0.0   # lowest single-word confidence


class VoiceRecognizer:
    def __init__(self, model_path: str, locations: Dict[str, dict], min_conf: float = 0.6):
        import vosk
        vosk.SetLogLevel(-1)
        if not os.path.isdir(model_path):
            raise FileNotFoundError(
                "Vosk model not found at %s. From the repo root run "
                "hci/crew_assist/scripts/download_vosk_model.sh "
                "(vosk-model-small-en-us-0.15, about 40 MB). "
                "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip" % model_path)
        self.vosk = vosk
        self.model = vosk.Model(model_path)
        self.phrases, self.dropped = build_grammar(
            locations, in_vocab=lambda w: self.model.vosk_model_find_word(w) >= 0)
        if self.dropped:
            log.warning("grammar: dropped %d phrases with out-of-vocabulary words: %s",
                        len(self.dropped), self.dropped[:5])
        self.grammar = json.dumps(self.phrases + ["[unk]"])
        self.decoy_set = set(decoy_phrases(locations))
        self.phrase_set = set(self.phrases) - self.decoy_set
        self.min_conf = min_conf

    def recognizer(self, rate: float):
        r = self.vosk.KaldiRecognizer(self.model, rate, self.grammar)
        r.SetWords(True)
        return r

    @staticmethod
    def _heard(result_json: str) -> Heard:
        res = json.loads(result_json)
        text = res.get("text", "").strip()
        words = res.get("result", [])
        conf = sum(w.get("conf", 0.0) for w in words) / len(words) if words else 0.0
        mn = min((w.get("conf", 0.0) for w in words), default=0.0)
        return Heard(text.replace("[unk]", "").strip(), conf,
                     "[unk]" in text or (bool(words) and not text), mn)

    def recognize_wav(self, path: str) -> Heard:
        with wave.open(path, "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                raise ValueError("%s: need mono 16-bit PCM WAV" % path)
            return self.recognize_pcm(wf.readframes(wf.getnframes()), wf.getframerate())

    def recognize_pcm(self, pcm: bytes, rate: float) -> Heard:
        """Mono 16-bit little-endian PCM at `rate` Hz. Vosk downsamples internally
        but refuses to UPsample, so <16 kHz input (e.g. 8 kHz phone audio) is
        linearly interpolated to 16 kHz first."""
        if rate < 16000:
            import numpy as np
            x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
            n = int(round(len(x) * 16000.0 / rate))
            pcm = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x) \
                .round().astype("<i2").tobytes()
            rate = 16000
        rec = self.recognizer(rate)
        texts: List[Heard] = []
        step = 8000  # bytes = 4000 frames, same chunking as before
        for i in range(0, len(pcm), step):
            if rec.AcceptWaveform(pcm[i:i + step]):
                h = self._heard(rec.Result())
                if h.text or h.unknown:
                    texts.append(h)
        h = self._heard(rec.FinalResult())
        if h.text or h.unknown:
            texts.append(h)
        if not texts:
            return Heard("", 0.0, False)
        if len(texts) == 1:
            return texts[0]
        # several utterances in one clip: joined, so the whole-phrase gate rejects them
        return Heard(" ".join(t.text for t in texts), min(t.confidence for t in texts),
                     any(t.unknown for t in texts), min(t.min_word_conf for t in texts))

    def listen_mic(self, device=None, rate: int = 16000) -> Iterator[Heard]:
        import queue
        import sounddevice as sd
        q: "queue.Queue[bytes]" = queue.Queue()

        def cb(indata, frames, t, status):  # noqa: ARG001
            q.put(bytes(indata))
        rec = self.recognizer(rate)
        with sd.RawInputStream(samplerate=rate, blocksize=8000, device=device, dtype="int16",
                               channels=1, callback=cb):
            while True:
                if rec.AcceptWaveform(q.get()):
                    h = self._heard(rec.Result())
                    if h.text or h.unknown:
                        yield h

    def accept(self, h: Heard) -> Tuple[bool, str]:
        """Gate a recognition before it reaches the parser.

        Stop words are fail-safe: honoured even with [unk] noise around them or
        low confidence. Everything else must be ONE whole grammar phrase (Vosk
        can emit fragments such as "the dock"), with no [unk], above min_conf."""
        t = normalize(h.text)
        if not t:
            return False, "nothing recognised"
        if t in STOP_WORDS:
            return True, ""
        if h.unknown:
            return False, "out-of-grammar speech ([unk]) - ignored"
        if t in getattr(self, "decoy_set", ()):
            hint = " (racks are 'rack alpha / bravo / charlie' by voice)" if "rack" in t else ""
            return False, "not a flyable target: %r%s" % (t, hint)
        if t not in self.phrase_set:
            return False, "not a whole command phrase: %r" % t
        if h.confidence < self.min_conf:
            return False, "low confidence %.2f < %.2f" % (h.confidence, self.min_conf)
        return True, ""


# ---------------------------------------------------------------- push-to-talk
class MicPTTSource:
    """Records from the microphone between start() and stop()."""

    def __init__(self, device=None, rate: int = 16000):
        self.device, self.rate = device, rate
        self._buf: List[bytes] = []
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd
        self._buf = []
        self._stream = sd.RawInputStream(samplerate=self.rate, blocksize=4000, device=self.device,
                                         dtype="int16", channels=1,
                                         callback=lambda d, f, t, st: self._buf.append(bytes(d)))
        self._stream.start()

    def stop(self) -> Optional[Tuple[bytes, int]]:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        return b"".join(self._buf), self.rate


class WavPTTSource:
    """Test stand-in: each start()/stop() pair 'records' the next WAV file."""

    def __init__(self, paths: List[str]):
        self.paths = list(paths)
        self.current: Optional[str] = None

    def start(self) -> None:
        self.current = self.paths.pop(0) if self.paths else None

    def stop(self) -> Optional[Tuple[bytes, int]]:
        if self.current is None:
            return None
        with wave.open(self.current, "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                raise ValueError("%s: need mono 16-bit PCM WAV" % self.current)
            return wf.readframes(wf.getnframes()), wf.getframerate()


def ptt_loop(vr: "VoiceRecognizer", source, on_heard, readline=None, echo=print) -> int:
    """Enter = start capture, Enter = stop and recognise; 'q' or EOF quits.
    Returns the number of utterances processed."""
    readline = readline or sys.stdin.readline
    n = 0
    while True:
        echo("[Enter] = talk   (q = quit)")
        line = readline()
        if line == "" or line.strip().lower() in ("q", "quit", "exit"):
            return n
        source.start()
        echo("  recording... [Enter] = send")
        readline()
        got = source.stop()
        if got is None:
            echo("  (no more audio)")
            return n
        pcm, rate = got
        n += 1
        on_heard(vr.recognize_pcm(pcm, rate), getattr(source, "current", None) or "ptt#%d" % n)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--wav", nargs="+", help="mono 16-bit WAV file(s), processed in order")
    src.add_argument("--mic", action="store_true", help="live microphone (sounddevice)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--locations", default=os.path.join(HERE, "locations.yaml"))
    p.add_argument("--min-conf", type=float, default=0.6)
    p.add_argument("--execute", action="store_true", help="send accepted intents to the robot")
    p.add_argument("--mock", action="store_true", help="with --execute: mock backend")
    p.add_argument("--yes", action="store_true", help="skip the y/N confirmation before motion")
    p.add_argument("--image-dir", default=os.path.join(HERE, "images"))
    p.add_argument("--log", default=None)
    p.add_argument("--device", default=None, help="sounddevice input device")
    p.add_argument("--print-grammar", action="store_true")
    p.add_argument("--ptt", action="store_true",
                   help="push-to-talk: Enter starts capture, Enter sends (with --mic, or --wav for tests)")
    a = p.parse_args(argv)
    if a.mic and a.yes and a.execute:
        # Broad synthetic test: 32/10,540 clips were confidently misheard as a
        # different rack (e.g. "three" -> "two" at conf 0.85-1.0). The y/N
        # read-back is the safeguard, so live speech may never skip it.
        p.error("--yes is not allowed with --mic --execute (confirm each motion)")
    if a.ptt and a.print_grammar:
        a.ptt = False

    locs = load_locations(a.locations)
    parser = IntentParser(locs)
    vr = VoiceRecognizer(a.model, locs, a.min_conf)
    if a.print_grammar:
        print("\n".join(vr.phrases))
        return 0

    session = None
    if a.execute:
        import astrobee_cmd as ac
        from sequencer import Sequencer
        from session import CrewSession
        backend = ac.MockBackend() if a.mock else ac.RosBackend("crew_assist_voice")
        commander = ac.AstrobeeCommander(backend)
        seq = Sequencer(commander, locs, image_dir=a.image_dir)

        def confirm(intent) -> bool:
            if a.yes:
                return True
            # read back the RESOLVED target and the raw words: the broad test showed
            # rack-number swaps ("three"->"two") at high confidence, so this prompt
            # is the real safeguard against a wrong destination.
            print("  heard %r -> execute %s %s? [y/N] " % (intent.text, intent.action,
                                                          intent.target or ""), end="", flush=True)
            return sys.stdin.readline().strip().lower() in ("y", "yes")
        session = CrewSession(parser, seq, commander, confirm=confirm,
                              echo=lambda m: print(m, flush=True))

    lg = logging.getLogger("crew_assist")   # configured after rospy.init_node
    lg.setLevel(logging.INFO)
    lg.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in [logging.StreamHandler()] + ([logging.FileHandler(a.log)] if a.log else []):
        h.setFormatter(fmt)
        lg.addHandler(h)

    def handle(h: Heard, label: str) -> bool:
        ok, why = vr.accept(h)
        log.info("HEARD %s: %r conf=%.2f unk=%s -> %s", label, h.text, h.confidence, h.unknown,
                 "accepted" if ok else why)
        if not ok:
            print("? (%s) %s" % (label, why), flush=True)
            return False
        if session is None:
            try:
                i = parser.parse(h.text)
                print("%s: %r -> %s %s" % (label, h.text, i.action, i.target or ""), flush=True)
                return True
            except ParseError as e:
                print("%s: %r -> ? %s" % (label, h.text, e), flush=True)
                return False
        intent = session.submit(h.text)
        if intent is None:
            return False
        if intent.action != "stop":
            session.wait_idle()     # WAV mode: one clip at a time, like a spoken script
            return session.results[-1].ok if session.results else False
        return True

    def live_heard(h: Heard, label: str = "mic") -> None:
        """Live (mic / PTT): never block the listener on a running sequence."""
        if session is not None and session.busy and normalize(h.text) in STOP_WORDS:
            session.submit(h.text)          # preempts immediately
            return
        if session is not None and session.busy:
            print("  busy - only 'emergency stop' (or stop/abort) is accepted while moving", flush=True)
            return
        ok, why = vr.accept(h)
        log.info("HEARD %s: %r conf=%.2f -> %s", label, h.text, h.confidence,
                 "accepted" if ok else why)
        if ok:
            session.submit(h.text) if session else print(h.text, flush=True)
        else:
            print("? %s" % why, flush=True)

    rc = 0
    try:
        if a.ptt:
            src_ = WavPTTSource(a.wav) if a.wav else MicPTTSource(a.device)
            print("push-to-talk. To stop the robot say 'EMERGENCY STOP' (or press Ctrl-C).", flush=True)
            ptt_loop(vr, src_, lambda h, lab: live_heard(h, os.path.basename(lab)),
                     echo=lambda m: print(m, flush=True))
            if session is not None:
                session.wait_idle()
        elif a.wav:
            for path in a.wav:
                if not handle(vr.recognize_wav(path), os.path.basename(path)) and session is not None:
                    print("aborting remaining clips", flush=True)
                    rc = 1
                    break
        else:
            print("listening... say a command. To stop the robot say 'EMERGENCY STOP' "
                  "(Ctrl-C = stop robot; Ctrl-C twice to quit)", flush=True)
            last_int = 0.0
            while True:
                try:
                    for h in vr.listen_mic(a.device):
                        live_heard(h)
                except KeyboardInterrupt:
                    import time
                    if session is not None:
                        session.stop("Ctrl-C")
                    if time.time() - last_int < 2.0:
                        break
                    last_int = time.time()
    except KeyboardInterrupt:
        if session is not None:
            session.stop("Ctrl-C")
        rc = 130
    if session is not None:
        session.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
