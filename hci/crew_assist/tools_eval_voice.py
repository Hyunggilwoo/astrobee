#!/usr/bin/env python3
"""Run ../voice_test clips through Vosk (grammar-constrained) -> gate -> parser and score.
Also scores an unconstrained (free-vocabulary) baseline for comparison."""
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from intent_parser import IntentParser, ParseError, load_locations
from voice_input import DEFAULT_MODEL, VoiceRecognizer

VT = os.path.join(HERE, "..", "voice_test")
man = json.load(open(os.path.join(VT, "manifest.json")))
locs = load_locations(os.path.join(HERE, "locations.yaml"))
parser = IntentParser(locs)
vr = VoiceRecognizer(DEFAULT_MODEL, locs)


def decide(h):
    ok, why = vr.accept(h)
    if not ok:
        return None, None, why
    try:
        i = parser.parse(h.text)
        return i.action, i.target, ""
    except ParseError as e:
        return None, None, "parse: %s" % str(e)[:40]


rows, stats = [], defaultdict(lambda: [0, 0])
for mode in ("grammar", "free"):
    if mode == "free":
        vr.grammar = None
        vr.recognizer = lambda rate: _free(rate)
        import vosk

        def _free(rate):
            r = vosk.KaldiRecognizer(vr.model, rate)
            r.SetWords(True)
            return r

    for m in man:
        h = vr.recognize_wav(os.path.join(VT, m["wav"]))
        act, tgt, why = decide(h)
        pos = m["action"] is not None
        correct = (act, tgt) == (m["action"], m["target"]) if pos else act is None
        false_accept = act is not None and (act, tgt) != (m["action"], m["target"])
        k = (mode, "positive" if pos else "negative", m["cond"])
        stats[k][0] += correct
        stats[k][1] += 1
        stats[(mode, "false_accept", m["cond"])][0] += false_accept
        stats[(mode, "false_accept", m["cond"])][1] += 1
        rows.append(
            dict(
                m,
                mode=mode,
                heard=h.text,
                conf=round(h.confidence, 2),
                unk=h.unknown,
                got_action=act,
                got_target=tgt,
                why=why,
                correct=correct,
                false_accept=false_accept,
            )
        )
json.dump(rows, open(os.path.join(VT, "results.json"), "w"), indent=1)
for k in sorted(stats):
    c, n = stats[k]
    print("%-8s %-13s %-6s %3d/%3d = %5.1f%%" % (k + (c, n, 100.0 * c / n)))
print("\nERRORS (grammar mode):")
for r in rows:
    if r["mode"] == "grammar" and not r["correct"]:
        print(
            "  %-22s %-6s said=%r heard=%r conf=%.2f -> %s %s %s"
            % (
                r["wav"],
                r["cond"],
                r["text"],
                r["heard"],
                r["conf"],
                r["got_action"],
                r["got_target"],
                r["why"],
            )
        )
