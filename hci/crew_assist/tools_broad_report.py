#!/usr/bin/env python3
"""results.csv -> results.md tables + accuracy_by_condition.png + accuracy_by_voice.png"""
import csv, os
from collections import OrderedDict, defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "voice_test", "broad")
R = list(csv.DictReader(open(os.path.join(OUT, "results.csv"))))
VALID = [r for r in R if r["expected"] != "REJECT"]
NEG = [r for r in R if r["expected"] == "REJECT"]
STOP = [r for r in VALID if r["expected"].startswith("stop")]
COND_ORDER = ["speed_1.00x", "speed_0.85x", "speed_1.15x", "pitch_-2", "pitch_+2",
              "white_20dB", "white_10dB", "white_5dB", "babble_20dB", "babble_10dB", "babble_5dB",
              "hum_20dB", "hum_10dB", "hum_5dB", "reverb", "phone_8k", "combo"]


def pct(a, b):
    return 100.0 * a / b if b else float("nan")


def stats(rows):
    v = [r for r in rows if r["expected"] != "REJECT"]
    n = [r for r in rows if r["expected"] == "REJECT"]
    s = [r for r in v if r["expected"].startswith("stop")]
    return dict(n=len(rows), valid=len(v),
                acc=pct(sum(r["outcome"] == "correct" for r in v), len(v)),
                rej=pct(sum(r["outcome"] == "rejected_ok" for r in n), len(n)),
                stop=pct(sum(r["outcome"] == "correct" for r in s), len(s)),
                wrong=sum(r["outcome"] == "WRONG" for r in rows))


def table(groups, title, key_name):
    lines = ["### " + title, "", "| %s | clips | valid-command accuracy | reject 18-20 | stop recall | WRONG action |" % key_name,
             "|---|---|---|---|---|---|"]
    for k, rows in groups.items():
        s = stats(rows)
        lines.append("| %s | %d | %.1f%% | %.1f%% | %.1f%% | %d |" % (k, s["n"], s["acc"], s["rej"], s["stop"], s["wrong"]))
    return "\n".join(lines) + "\n"


def group(fn, order=None):
    g = defaultdict(list)
    for r in R:
        g[fn(r)].append(r)
    keys = order or sorted(g)
    return OrderedDict((k, g[k]) for k in keys if k in g)


by_cond = group(lambda r: r["cond"], COND_ORDER)
by_voice = group(lambda r: r["voice"])
by_voice = OrderedDict(sorted(by_voice.items(), key=lambda kv: -stats(kv[1])["acc"]))
by_accent = group(lambda r: r["accent"])
by_accent = OrderedDict(sorted(by_accent.items(), key=lambda kv: -stats(kv[1])["acc"]))
by_gender = group(lambda r: r["gender"])
by_phrase = group(lambda r: "%02d %s" % (int(r["phrase_id"]), r["phrase"]))
voices = sorted({r["voice"] for r in R})

# ---- charts
fig, ax = plt.subplots(figsize=(12, 5))
labels = list(by_cond)
acc = [stats(by_cond[k])["acc"] for k in labels]
stp = [stats(by_cond[k])["stop"] for k in labels]
x = range(len(labels))
ax.bar([i - 0.2 for i in x], acc, 0.4, label="valid commands correct (1-17)")
ax.bar([i + 0.2 for i in x], stp, 0.4, label="stop-word recall (15-17)")
for i, k in enumerate(labels):
    w = stats(by_cond[k])["wrong"]
    if w:
        ax.text(i, 2, "%d wrong" % w, rotation=90, ha="center", va="bottom", fontsize=8, color="darkred")
ax.set_xticks(list(x)); ax.set_xticklabels(labels, rotation=45, ha="right")
ax.set_ylim(0, 105); ax.set_ylabel("%"); ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.32), ncol=2)
ax.set_title("voice_input.py (Vosk small + closed grammar): accuracy by condition — %d voices, %d clips" % (len(voices), len(R)))
ax.grid(axis="y", alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "accuracy_by_condition.png"), dpi=130)

fig, ax = plt.subplots(figsize=(12, 5))
vl = list(by_voice)
ax.bar(range(len(vl)), [stats(by_voice[k])["acc"] for k in vl], color="tab:blue", label="valid commands correct")
ax.plot(range(len(vl)), [stats(by_voice[k])["stop"] for k in vl], "o", color="tab:orange", label="stop recall")
ax.set_xticks(range(len(vl)))
ax.set_xticklabels(["%s (%s)" % (k, by_voice[k][0]["accent"]) for k in vl], rotation=60, ha="right", fontsize=8)
ax.set_ylim(0, 105); ax.set_ylabel("%"); ax.legend(loc="lower left"); ax.grid(axis="y", alpha=.3)
ax.set_title("Accuracy by voice (all 17 conditions pooled)")
fig.tight_layout(); fig.savefig(os.path.join(OUT, "accuracy_by_voice.png"), dpi=130)

# ---- markdown pieces
S = stats(R)
W = [r for r in R if r["outcome"] == "WRONG"]
wrong_lines = ["| voice | condition | said | heard (conf / min word conf) | would execute |", "|---|---|---|---|---|"]
for r in sorted(W, key=lambda r: (r["phrase"], r["voice"])):
    wrong_lines.append("| %s | %s %s | %s | %s (%s / %s) | %s |" % (r["voice"], r["cond"],
                       "(" + r["level"] + ")" if r["cond"] == "combo" else "", r["phrase"], r["heard"],
                       r["conf"], r["min_wconf"], r["got"]))
worst = sorted(((k, stats(v)) for k, v in by_phrase.items()), key=lambda kv: kv[1]["acc"] if kv[1]["valid"] else 200)
open(os.path.join(OUT, "tables.md"), "w").write("\n".join([
    "## Overall", "", "| clips | voices | valid accuracy | reject 18-20 | stop recall | WRONG |", "|---|---|---|---|---|---|",
    "| %d | %d | %.1f%% (%d) | %.2f%% (%d) | %.1f%% (%d) | %d (%.2f%%) |" % (
        S["n"], len(voices), S["acc"], S["valid"], S["rej"], len(NEG), S["stop"], len(STOP), S["wrong"], pct(S["wrong"], S["n"])),
    "", table(by_cond, "By condition (one factor at a time; combo = random speed+pitch+noise@10dB, 50% reverb, 50% phone)", "condition"),
    table(by_accent, "By accent group", "accent"),
    table(by_gender, "By gender (? = multi-speaker model, unlabeled)", "gender"),
    table(by_voice, "By voice", "voice"),
    table(OrderedDict((k, by_phrase[k]) for k, _ in worst), "By phrase (worst first; 18-20 = must reject)", "phrase"),
    "### Every WRONG-action case", "", "\n".join(wrong_lines), ""]))
print("ok", S)
