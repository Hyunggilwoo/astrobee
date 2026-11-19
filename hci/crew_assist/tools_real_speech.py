#!/usr/bin/env python3
"""Evaluate voice_input.py on REAL human speech: Google Speech Commands v0.02 (CC BY 4.0).

  python3 tools_real_speech.py eval  <tag>   # -> ../voice_test/real/real_<tag>.csv
  python3 tools_real_speech.py splice <tag>  # hybrid: TTS carrier + REAL digit -> splice_<tag>.csv

Sampling: words below, one clip per speaker, official test split first, then
validation, then train, up to N per word (fixed seed). Conditions: clean, and
dataset _background_noise_ at 10 dB and 5 dB SNR (noise file + offset random).
"""
import csv, json, os, random, sys, wave, zlib
from collections import defaultdict
from multiprocessing import Pool
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SC = os.path.join(ROOT, "datasets", "speech_commands")
OUT = os.path.join(ROOT, "voice_test", "real")
sys.path.insert(0, HERE)
WORDS = ["stop", "go", "off", "one", "two", "three", "four", "five", "no", "yes"]
N_PER_WORD = 400
CONDS = [("clean", None), ("bg_10dB", 10), ("bg_5dB", 5)]
NOISE_FILES = ["doing_the_dishes.wav", "dude_miaowing.wav", "exercise_bike.wav",
               "running_tap.wav", "pink_noise.wav", "white_noise.wav"]
DIGITS = ["one", "two", "three"]


def read(path):
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768, w.getframerate()


def to_pcm(x):
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def speech_rms(x):
    f = x[: len(x) // 320 * 320].reshape(-1, 320)
    e = np.sqrt((f ** 2).mean(1))
    act = f[e > 0.1 * e.max()] if e.max() > 0 else f
    return float(np.sqrt((act ** 2).mean()) + 1e-12)


_NOISE = None


def noise(n, rng):
    global _NOISE
    if _NOISE is None:
        _NOISE = {f: read(os.path.join(SC, "_background_noise_", f))[0] for f in NOISE_FILES}
    f = rng.choice(NOISE_FILES)
    z = _NOISE[f]
    o = rng.randint(0, len(z) - n - 1)
    return z[o:o + n], f


def mix(x, snr, rng):
    nz, f = noise(len(x), rng)
    nz = nz * (speech_rms(x) / (np.sqrt((nz ** 2).mean()) + 1e-12)) / (10 ** (snr / 20))
    y = x + nz
    pk = np.abs(y).max()
    return (y / pk * 0.9 if pk > 0.99 else y), f


def sample():
    split = {}
    for name, rank in (("testing_list.txt", 0), ("validation_list.txt", 1)):
        for line in open(os.path.join(SC, name)):
            split[line.strip()] = rank
    rng = random.Random(1234)
    picks = []
    for w in WORDS:
        files = sorted(os.listdir(os.path.join(SC, w)))
        rng.shuffle(files)
        files.sort(key=lambda f: split.get("%s/%s" % (w, f), 2))
        seen = set()
        for f in files:
            spk = f.split("_nohash_")[0]
            if spk in seen:
                continue
            seen.add(spk)
            picks.append((w, f, {0: "test", 1: "validation", 2: "train"}[split.get("%s/%s" % (w, f), 2)]))
            if len([p for p in picks if p[0] == w]) >= N_PER_WORD:
                break
    return picks


_VR = _P = _DG = None


def _init():
    global _VR, _P, _DG
    import vosk
    from intent_parser import IntentParser, load_locations
    from voice_input import VoiceRecognizer, DEFAULT_MODEL
    locs = load_locations(os.path.join(HERE, "locations.yaml"))
    _VR = VoiceRecognizer(DEFAULT_MODEL, locs)
    _P = IntentParser(locs)
    _DG = json.dumps(DIGITS + ["[unk]"])


def decide(h):
    from intent_parser import ParseError
    ok, why = _VR.accept(h)
    if not ok:
        return "", why
    try:
        i = _P.parse(h.text)
        return ("%s %s" % (i.action, i.target or "")).strip(), ""
    except ParseError as e:
        return "", "parse: " + str(e)[:40]


def digit_only(pcm):
    import vosk
    r = vosk.KaldiRecognizer(_VR.model, 16000, _DG)
    for i in range(0, len(pcm), 8000):
        r.AcceptWaveform(pcm[i:i + 8000])
    return json.loads(r.FinalResult()).get("text", "").strip()


def _eval_one(item):
    w, f, split = item
    x, sr = read(os.path.join(SC, w, f))
    rng = random.Random(zlib.crc32(f.encode()))
    rows = []
    for cond, snr in CONDS:
        y, nf = (x, "") if snr is None else mix(x, snr, rng)
        pcm = to_pcm(y)
        h = _VR.recognize_pcm(pcm, sr)
        got, why = decide(h)
        dg = digit_only(pcm) if w in DIGITS + ["four", "five"] else ""
        rows.append(dict(word=w, file=f, speaker=f.split("_nohash_")[0], split=split, cond=cond,
                         noise=nf, heard=h.text, conf=round(h.confidence, 3),
                         min_wconf=round(h.min_word_conf, 3), unk=h.unknown, accepted=got,
                         why=why, digit_grammar=dg))
    return rows


def evaluate(tag):
    os.makedirs(OUT, exist_ok=True)
    picks = sample()
    rows = []
    with Pool(7, initializer=_init) as pool:
        for r in pool.imap_unordered(_eval_one, picks, chunksize=20):
            rows += r
    rows.sort(key=lambda r: (r["word"], r["file"], r["cond"]))
    path = os.path.join(OUT, "real_%s.csv" % tag)
    with open(path, "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    json.dump(picks, open(os.path.join(OUT, "sample_list.json"), "w"))
    print("wrote", path, len(rows), "rows from", len(picks), "clips")


# ---------------------------------------------------------------- hybrid splice
def trim(x, sr):
    """Cut a 1 s Speech Commands clip to the spoken word (energy-based)."""
    hop = sr // 100
    f = x[: len(x) // hop * hop].reshape(-1, hop)
    e = np.sqrt((f ** 2).mean(1))
    on = np.where(e > 0.15 * e.max())[0]
    a, b = max(0, on[0] - 3), min(len(e), on[-1] + 4)
    return x[a * hop: b * hop]


def _splice_one(args):
    voice_label, carrier_path, word, f, snr = args
    from tools_broad_voice import read_wav, resample
    c, csr = read_wav(carrier_path)
    c = resample(c, csr, 16000)
    x, sr = read(os.path.join(SC, word, f))
    d = trim(x, sr)
    d = d * (speech_rms(c) / speech_rms(d))          # level-match the real word
    gap = np.zeros(int(0.05 * 16000), np.float32)
    pad = np.zeros(int(0.3 * 16000), np.float32)
    y = np.concatenate([pad, c, gap, d, pad])
    pk = np.abs(y).max()
    y = y / pk * 0.9 if pk > 0.99 else y
    if snr is not None:
        y, _ = mix(y.astype(np.float32), snr, random.Random(zlib.crc32((f + str(snr)).encode())))
    h = _VR.recognize_pcm(to_pcm(y), 16000)
    got, why = decide(h)
    return dict(cond="clean" if snr is None else "bg_%ddB" % snr, carrier_voice=voice_label,
                carrier=os.path.basename(carrier_path), word=word, file=f,
                heard=h.text, conf=round(h.confidence, 3), accepted=got, why=why)


def splice(tag, carriers, words_map, n=150):
    """carriers: {label: wav of 'move to rack'}; words_map: {word: expected target}."""
    picks = [p for p in sample() if p[0] in words_map]
    rng = random.Random(99)
    jobs = []
    for w in words_map:
        fs = [p[1] for p in picks if p[0] == w][:n]
        for f in fs:
            lab = rng.choice(sorted(carriers))
            for snr in (None, 10, 5):
                jobs.append((lab, carriers[lab], w, f, snr))
    with Pool(7, initializer=_init) as pool:
        rows = list(pool.imap_unordered(_splice_one, jobs, chunksize=10))
    path = os.path.join(OUT, "splice_%s.csv" % tag)
    with open(path, "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    print("wrote", path, len(rows))


def make_carriers():
    """Synthetic carriers 'move to rack' / 'photograph rack' (Piper, 6 voices)."""
    from piper import PiperVoice, SynthesisConfig
    d = os.path.join(OUT, "carriers")
    os.makedirs(d, exist_ok=True)
    voices = {"lessac": ("en_US-lessac-medium", None), "ryan": ("en_US-ryan-medium", None),
              "amy": ("en_US-amy-medium", None), "alan": ("en_GB-alan-medium", None),
              "jenny_dioco": ("en_GB-jenny_dioco-medium", None), "l2_asi": ("en_US-l2arctic-medium", 10)}
    out = {}
    for lab, (m, spk) in voices.items():
        v = PiperVoice.load(os.path.join(ROOT, "models", "piper", m + ".onnx"))
        for text in ("move to rack", "photograph rack"):
            path = os.path.join(d, "%s__%s.wav" % (lab, text.replace(" ", "_")))
            with wave.open(path, "wb") as wf:
                v.synthesize_wav(text, wf, SynthesisConfig(speaker_id=spk))
            out["%s/%s" % (lab, text)] = path
    return out


if __name__ == "__main__":
    if sys.argv[1] == "eval":
        evaluate(sys.argv[2])
    elif sys.argv[1] == "splice":
        splice(sys.argv[2], make_carriers(), {"one": "rack_one", "two": "rack_two", "three": "rack_three"})
