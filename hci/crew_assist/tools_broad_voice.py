#!/usr/bin/env python3
"""Broad synthetic-voice robustness test for voice_input.py.

  python3 tools_broad_voice.py synth   # Piper -> ../voice_test/broad/base/*.wav
  python3 tools_broad_voice.py eval    # augment in memory -> Vosk -> gate -> parser
  python3 tools_broad_voice.py report  # results.md + charts

Design: one-factor-at-a-time augmentations of every (voice, phrase) clip,
plus one random "combo" stress clip per (voice, phrase).
"""
import csv, io, json, os, random, subprocess, sys, wave
from collections import defaultdict
from multiprocessing import Pool

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
OUT = os.path.join(ROOT, "voice_test", "broad")
BASE = os.path.join(OUT, "base")
PIPER = os.path.join(ROOT, "models", "piper")
sys.path.insert(0, HERE)

PHRASES = [  # (text, expected action, expected target); action None => must be rejected
    ("undock", "undock", None), ("leave the dock", "undock", None),
    ("go to mid jem", "go_to", "mid_jem"), ("fly to the first rack", "go_to", "rack_one"),
    ("move to rack two", "go_to", "rack_two"), ("go to the far rack", "go_to", "rack_three"),
    ("fly to rack three", "go_to", "rack_three"), ("photograph rack one", "photograph", "rack_one"),
    ("take a picture of the opposite rack", "photograph", "rack_two"),
    ("take a photo of the third rack", "photograph", "rack_three"),
    ("photograph the center of jem", "photograph", "mid_jem"),
    ("return to dock", "return_to_dock", None), ("go back to the dock", "return_to_dock", None),
    ("go home", "return_to_dock", None),
    ("stop", "stop", None), ("abort", "stop", None), ("emergency stop", "stop", None),
    ("go to the cupola", None, None), ("open the hatch", None, None),
    ("photograph this panel", None, None),
]
# (label, model, speaker_id, accent group, gender, note)
VOICES = [
    ("lessac", "en_US-lessac-medium", None, "US", "F", ""),
    ("amy", "en_US-amy-medium", None, "US", "F", ""),
    ("ryan", "en_US-ryan-medium", None, "US", "M", ""),
    ("joe", "en_US-joe-medium", None, "US", "M", ""),
    ("kristin", "en_US-kristin-medium", None, "US", "F", ""),
    ("hfc_female", "en_US-hfc_female-medium", None, "US", "F", ""),
    ("hfc_male", "en_US-hfc_male-medium", None, "US", "M", ""),
    ("john", "en_US-john-medium", None, "US", "M", ""),
    ("norman", "en_US-norman-medium", None, "US", "M", ""),
    ("libritts_r_0", "en_US-libritts_r-medium", 0, "US", "?", "LibriTTS-R spk 0"),
    ("libritts_r_100", "en_US-libritts_r-medium", 100, "US", "?", "LibriTTS-R spk 100"),
    ("libritts_r_500", "en_US-libritts_r-medium", 500, "US", "?", "LibriTTS-R spk 500"),
    ("arctic_jmk", "en_US-arctic-medium", 8, "Canadian", "M", "CMU ARCTIC jmk"),
    ("alan", "en_GB-alan-medium", None, "UK", "M", ""),
    ("cori", "en_GB-cori-medium", None, "UK", "F", ""),
    ("northern_eng_male", "en_GB-northern_english_male-medium", None, "UK", "M", ""),
    ("southern_eng_female", "en_GB-southern_english_female-low", None, "UK", "F", "low quality model"),
    ("alba", "en_GB-alba-medium", None, "Scottish", "F", ""),
    ("arctic_awb", "en_US-arctic-medium", 0, "Scottish", "M", "CMU ARCTIC awb"),
    ("jenny_dioco", "en_GB-jenny_dioco-medium", None, "Irish", "F", ""),
    ("vctk_p247", "en_GB-vctk-medium", 5, "UK (VCTK)", "?", "VCTK p247"),
    ("vctk_p261", "en_GB-vctk-medium", 6, "UK (VCTK)", "?", "VCTK p261"),
    ("vctk_p283", "en_GB-vctk-medium", 8, "UK (VCTK)", "?", "VCTK p283"),
    ("arctic_ksp", "en_US-arctic-medium", 3, "Indian", "M", "CMU ARCTIC ksp"),
    ("l2_asi", "en_US-l2arctic-medium", 10, "Indian (L2, Hindi)", "M", "L2-ARCTIC ASI"),
    ("l2_svbi", "en_US-l2arctic-medium", 2, "Indian (L2, Hindi)", "F", "L2-ARCTIC SVBI"),
    ("l2_bwc", "en_US-l2arctic-medium", 20, "L2 Mandarin", "M", "L2-ARCTIC BWC"),
    ("l2_hjk", "en_US-l2arctic-medium", 11, "L2 Korean", "F", "L2-ARCTIC HJK"),
    ("l2_mbmps", "en_US-l2arctic-medium", 7, "L2 Spanish", "F", "L2-ARCTIC MBMPS"),
    ("kusal", "en_US-kusal-medium", None, "other (unverified)", "M", ""),
    ("reza_ibrahim", "en_US-reza_ibrahim-medium", None, "other (unverified)", "M", ""),
]
SPEEDS = {"0.85x": 1 / 0.85, "1.00x": 1.0, "1.15x": 1 / 1.15}   # piper length_scale
BABBLE_TEXT = [
    "The weather report says it will rain later this afternoon.",
    "I think the meeting moved to Thursday after lunch.",
    "Could you pass me the blue folder on the shelf?",
    "We should order more coffee before the weekend.",
    "My brother is driving up from the city tonight.",
    "The train was late again because of the signal problem.",
    "She finished the report and sent it to everyone.",
    "Let's try the new restaurant near the station.",
]
RATE = 16000

# PHRASESET=nato: same 20 commands, racks spoken as alpha/bravo/charlie
# (voice grammar since 19 Nov 2026). Old numbered phrases stay the default set.
PHRASESET = os.environ.get("PHRASESET", "orig")
if PHRASESET == "nato":
    _NATO = {3: ("fly to rack alpha", "go_to", "rack_one"),
             4: ("move to rack bravo", "go_to", "rack_two"),
             5: ("go to the charlie rack", "go_to", "rack_three"),
             6: ("fly to rack charlie", "go_to", "rack_three"),
             7: ("photograph rack alpha", "photograph", "rack_one"),
             8: ("take a picture of the bravo rack", "photograph", "rack_two"),
             9: ("take a photo of the charlie rack", "photograph", "rack_three")}
    PHRASES = [_NATO.get(i, p) for i, p in enumerate(PHRASES)]
    BASE = os.path.join(OUT, "base_nato")
RESULTS = os.environ.get("RESULTS", "results.csv")


# ---------------------------------------------------------------- synth
def _synth_voice(v):
    from piper import PiperVoice, SynthesisConfig
    label, model, spk = v[0], v[1], v[2]
    voice = PiperVoice.load(os.path.join(PIPER, model + ".onnx"))
    n = 0
    for i, (text, _, _) in enumerate(PHRASES):
        for sp, ls in SPEEDS.items():
            path = os.path.join(BASE, "%s__p%02d__%s.wav" % (label, i, sp))
            if os.path.exists(path):
                continue
            with wave.open(path, "wb") as wf:
                voice.synthesize_wav(text, wf, SynthesisConfig(speaker_id=spk, length_scale=ls))
            n += 1
    # babble material from this voice (neutral sentences)
    for j, text in enumerate(BABBLE_TEXT[:3]):
        path = os.path.join(OUT, "babble_src", "%s_%d.wav" % (label, j))
        if not os.path.exists(path):
            with wave.open(path, "wb") as wf:
                voice.synthesize_wav(BABBLE_TEXT[(j + hash(label)) % len(BABBLE_TEXT)], wf,
                                     SynthesisConfig(speaker_id=spk))
    return label, n


def synth():
    os.makedirs(BASE, exist_ok=True)
    os.makedirs(os.path.join(OUT, "babble_src"), exist_ok=True)
    with Pool(4) as pool:
        for label, n in pool.imap_unordered(_synth_voice, VOICES):
            print("synth", label, n, flush=True)


# ---------------------------------------------------------------- audio utils
def read_wav(path):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    return x, sr


def resample(x, sr, to):
    if sr == to:
        return x
    from scipy.signal import resample_poly
    from math import gcd
    g = gcd(int(sr), int(to))
    return resample_poly(x, int(to) // g, int(sr) // g).astype(np.float32)


def to_pcm(x):
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def pad(x, sec=0.4):
    z = np.zeros(int(sec * RATE), np.float32)
    return np.concatenate([z, x, z])


def rms(x):
    return float(np.sqrt(np.mean(x ** 2)) + 1e-12)


def speech_rms(x):
    """RMS over the louder frames only (ignores leading/trailing silence)."""
    f = x[: len(x) // 320 * 320].reshape(-1, 320)
    e = np.sqrt((f ** 2).mean(1))
    act = f[e > 0.1 * e.max()]
    return float(np.sqrt((act ** 2).mean()) + 1e-12)


def mix(x, noise, snr_db, rng):
    if len(noise) < len(x):
        noise = np.tile(noise, int(np.ceil(len(x) / len(noise))))
    o = rng.randint(0, len(noise) - len(x))
    n = noise[o:o + len(x)]
    n = n * (speech_rms(x) / rms(n)) / (10 ** (snr_db / 20))
    y = x + n
    peak = np.abs(y).max()
    return y / peak * 0.9 if peak > 0.99 else y


def white_noise(n, rng):
    return np.random.default_rng(rng.randint(0, 1 << 30)).standard_normal(n).astype(np.float32)


def hum_noise(n, rng):
    """Fan + mains hum: 60 Hz with harmonics plus low-passed broadband fan noise."""
    t = np.arange(n) / RATE
    h = sum((0.6 / k) * np.sin(2 * np.pi * 60 * k * t + rng.random() * 6.28) for k in range(1, 8))
    from scipy.signal import butter, lfilter
    b, a = butter(2, 800 / (RATE / 2))
    fan = lfilter(b, a, np.random.default_rng(rng.randint(0, 1 << 30)).standard_normal(n))
    fan = fan / (rms(fan) + 1e-9) * 0.5
    blade = 1 + 0.3 * np.sin(2 * np.pi * 25 * t)          # blade-pass modulation
    return (h + fan * blade).astype(np.float32)


_BABBLE = None


def babble_noise(n, rng):
    global _BABBLE
    if _BABBLE is None:
        src = sorted(os.listdir(os.path.join(OUT, "babble_src")))
        clips = [resample(*read_wav(os.path.join(OUT, "babble_src", f)), RATE) for f in src]
        r = random.Random(7)
        L = 20 * RATE
        tracks = []
        for _ in range(6):                                  # 6 overlapping talkers
            tr = np.zeros(0, np.float32)
            while len(tr) < L:
                c = clips[r.randrange(len(clips))]
                tr = np.concatenate([tr, c / (rms(c) + 1e-9), np.zeros(r.randint(0, 1600), np.float32)])
            tracks.append(tr[:L])
        _BABBLE = np.sum(tracks, 0).astype(np.float32)
    o = rng.randint(0, len(_BABBLE) - n - 1)
    return _BABBLE[o:o + n]


NOISES = {"white": white_noise, "babble": babble_noise, "hum": hum_noise}


def reverb(x, rng, rt60=0.4, wet=0.35):
    from scipy.signal import fftconvolve
    n = int(rt60 * RATE)
    t = np.arange(n) / RATE
    ir = np.random.default_rng(rng.randint(0, 1 << 30)).standard_normal(n) * np.exp(-6.9 * t / rt60)
    ir[0] = 0
    ir = ir / np.sqrt((ir ** 2).sum())
    y = (1 - wet) * x + wet * fftconvolve(x, ir)[: len(x)]
    return (y / (np.abs(y).max() + 1e-9) * 0.9).astype(np.float32)


def phone(x):
    """Telephone band: 300-3400 Hz band-pass, 8 kHz, mu-law-ish 8-bit quantisation."""
    from scipy.signal import butter, sosfilt
    sos = butter(4, [300, 3400], btype="band", fs=RATE, output="sos")
    y = resample(sosfilt(sos, x).astype(np.float32), RATE, 8000)
    mu = 255.0
    c = np.sign(y) * np.log1p(mu * np.abs(np.clip(y, -1, 1))) / np.log1p(mu)
    c = np.round(c * 127) / 127
    y = np.sign(c) * ((1 + mu) ** np.abs(c) - 1) / mu
    return y.astype(np.float32), 8000


def pitch(x, semitones):
    r = 2 ** (semitones / 12)
    p = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "1",
                        "-i", "pipe:0", "-af", "rubberband=pitch=%.6f" % r, "-f", "s16le",
                        "-ar", str(RATE), "-ac", "1", "pipe:1"], input=to_pcm(x),
                       capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype="<i2").astype(np.float32) / 32768


def conditions(label, i, rng):
    """Yield (cond_name, factor, level, pcm, rate) for one (voice, phrase)."""
    clips = {sp: resample(*read_wav(os.path.join(BASE, "%s__p%02d__%s.wav" % (label, i, sp))), RATE)
             for sp in SPEEDS}
    x = pad(clips["1.00x"])
    for sp in SPEEDS:
        yield "speed_" + sp, "speed", sp, pad(clips[sp]), RATE
    for st in (-2, 2):
        yield "pitch_%+d" % st, "pitch", "%+d st" % st, pitch(x, st), RATE
    for nt, fn in NOISES.items():
        for snr in (20, 10, 5):
            yield "%s_%ddB" % (nt, snr), "noise:" + nt, "%d dB" % snr, mix(x, fn(len(x) * 3, rng), snr, rng), RATE
    yield "reverb", "reverb", "rt60 0.4s", reverb(x, rng), RATE
    y, r = phone(x)
    yield "phone_8k", "phone", "8 kHz band", y, r
    # combo stress clip: random speed + pitch + noise@10dB, 50% reverb, 50% phone
    sp = rng.choice(list(SPEEDS))
    y = pad(clips[sp])
    st = rng.choice([-2, 0, 2])
    if st:
        y = pitch(y, st)
    nt = rng.choice(list(NOISES))
    y = mix(y, NOISES[nt](len(y) * 3, rng), 10, rng)
    desc = "%s/%+dst/%s10dB" % (sp, st, nt)
    if rng.random() < 0.5:
        y = reverb(y, rng); desc += "/rev"
    r = RATE
    if rng.random() < 0.5:
        y, r = phone(y); desc += "/phone"
    yield "combo", "combo", desc, y, r


# ---------------------------------------------------------------- eval
_VR = _PARSER = None


def _init():
    global _VR, _PARSER
    from intent_parser import IntentParser, load_locations
    from voice_input import VoiceRecognizer, DEFAULT_MODEL
    locs = load_locations(os.path.join(HERE, "locations.yaml"))
    _VR = VoiceRecognizer(DEFAULT_MODEL, locs)
    _PARSER = IntentParser(locs)


def decide(h):
    from intent_parser import ParseError
    ok, why = _VR.accept(h)
    if not ok:
        return None, None, why
    try:
        it = _PARSER.parse(h.text)
        return it.action, it.target, ""
    except ParseError as e:
        return None, None, "parse: " + str(e)[:50]


def _eval_voice(v):
    label = v[0]
    rng = random.Random(__import__("zlib").crc32(label.encode()))
    rows = []
    for i, (text, act, tgt) in enumerate(PHRASES):
        for cond, factor, level, y, r in conditions(label, i, rng):
            h = _VR.recognize_pcm(to_pcm(y), r)
            ga, gt, why = decide(h)
            valid = act is not None
            if valid:
                outcome = "correct" if (ga, gt) == (act, tgt) else ("rejected" if ga is None else "WRONG")
            else:
                outcome = "rejected_ok" if ga is None else "WRONG"
            rows.append(dict(voice=label, model=v[1], accent=v[3], gender=v[4], phrase_id=i + 1,
                             phrase=text, expected="%s %s" % (act, tgt or "") if valid else "REJECT",
                             cond=cond, factor=factor, level=level, heard=h.text,
                             conf=round(h.confidence, 3), min_wconf=round(h.min_word_conf, 3), unk=h.unknown,
                             got="%s %s" % (ga, gt or "") if ga else "", why=why, outcome=outcome))
    return label, rows


def evaluate():
    rows = []
    with Pool(6, initializer=_init) as pool:
        for label, r in pool.imap_unordered(_eval_voice, VOICES):
            rows += r
            print("eval", label, len(r), sum(x["outcome"] == "WRONG" for x in r), flush=True)
    with open(os.path.join(OUT, RESULTS), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("rows", len(rows))


if __name__ == "__main__":
    {"synth": synth, "eval": evaluate}[sys.argv[1]]()
