#!/usr/bin/env python3
"""Synthesize the voice test set with Piper TTS (offline) -> ../voice_test/.
Writes clean clips plus copies with white noise at 10 dB SNR, and manifest.json."""
import json, os, random, struct, wave
from piper import PiperVoice

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "voice_test")
VOICES = ["en_US-lessac-medium", "en_US-ryan-medium", "en_GB-alan-medium"]
# (utterance, expected action, expected target) ; action None = must be rejected
CASES = [
    ("undock", "undock", None),
    ("leave the dock", "undock", None),
    ("stop", "stop", None),
    ("stop all motion", "stop", None),
    ("abort", "stop", None),
    ("return to dock", "return_to_dock", None),
    ("go home", "return_to_dock", None),
    ("go back to the dock", "return_to_dock", None),
    ("go to mid jem", "go_to", "mid_jem"),
    ("fly to the middle of jem", "go_to", "mid_jem"),
    ("move to center of jem", "go_to", "mid_jem"),
    ("move to kibo", None, None),            # typed-only alias; voice must reject
    ("go to rack alpha", "go_to", "rack_one"),
    ("fly to rack bravo", "go_to", "rack_two"),
    ("go to rack charlie", "go_to", "rack_three"),
    ("photograph rack alpha", "photograph", "rack_one"),
    ("photograph rack bravo", "photograph", "rack_two"),
    ("photograph rack charlie", "photograph", "rack_three"),
    ("take a picture of rack bravo", "photograph", "rack_two"),
    ("take a picture of the bravo rack", "photograph", "rack_two"),
    ("take a photo of the alpha rack", "photograph", "rack_one"),
    ("photograph the charlie rack", "photograph", "rack_three"),
    # out-of-grammar: must be rejected
    ("open the hatch", None, None),
    ("go to the cupola", None, None),
    ("photograph this panel", None, None),
    ("what time is it", None, None),
    ("turn on the lights", None, None),
    ("photograph rack four", None, None),
    ("photograph rack one", None, None),     # numbered racks are typed-only now
    ("fly to rack three", None, None),
]


def add_noise(src, dst, snr_db=10.0, seed=0):
    rnd = random.Random(seed)
    with wave.open(src, "rb") as w:
        params, n = w.getparams(), w.getnframes()
        s = struct.unpack("<%dh" % n, w.readframes(n))
    p_sig = sum(x * x for x in s) / max(1, n)
    sigma = (p_sig / (10 ** (snr_db / 10))) ** 0.5
    y = [max(-32768, min(32767, int(x + rnd.gauss(0, sigma)))) for x in s]
    with wave.open(dst, "wb") as w:
        w.setparams(params)
        w.writeframes(struct.pack("<%dh" % n, *y))


def main():
    os.makedirs(OUT, exist_ok=True)
    manifest = []
    for v in VOICES:
        voice = PiperVoice.load(os.path.join(HERE, "..", "models", "piper", v + ".onnx"))
        for i, (text, act, tgt) in enumerate(CASES):
            base = "%s_%02d" % (v.split("-")[1], i)
            clean = os.path.join(OUT, base + "_clean.wav")
            with wave.open(clean, "wb") as wf:
                voice.synthesize_wav(text, wf)
            noisy = os.path.join(OUT, base + "_snr10.wav")
            add_noise(clean, noisy, 10.0, seed=i)
            for path, cond in ((clean, "clean"), (noisy, "snr10")):
                manifest.append({"wav": os.path.basename(path), "text": text, "voice": v,
                                 "cond": cond, "action": act, "target": tgt})
    json.dump(manifest, open(os.path.join(OUT, "manifest.json"), "w"), indent=1)
    print("wrote %d clips" % len(manifest))


if __name__ == "__main__":
    main()
