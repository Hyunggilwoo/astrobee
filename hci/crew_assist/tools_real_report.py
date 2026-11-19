"""Summarise real-speech (Speech Commands) + splice + broad before/after runs; draw chart."""
import csv, json, os, collections
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
V = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "voice_test")
rd = lambda p: list(csv.DictReader(open(os.path.join(V, p))))
CONDS = ["clean", "bg_10dB", "bg_5dB"]
out = {}
for tag in ["before", "after"]:
    R = rd("real/real_%s.csv" % tag); d = {}
    for c in CONDS:
        rc = [r for r in R if r["cond"] == c]
        st = [r for r in rc if r["word"] == "stop"]
        non = [r for r in rc if r["word"] != "stop"]
        acc = lambda r: r["accepted"].strip()
        motion = [r for r in non if acc(r) and not acc(r).startswith("stop")]
        falsestop = collections.Counter(r["word"] for r in non if acc(r).startswith("stop"))
        miss = collections.Counter(r["heard"] for r in st if not acc(r).startswith("stop"))
        conf = {w: collections.Counter(r["digit_grammar"] for r in rc if r["word"] == w) for w in ["one","two","three","four","five"]}
        d[c] = dict(stop_n=len(st), stop_hit=sum(acc(r).startswith("stop") for r in st),
                    non_n=len(non), motion_fa=len(motion), motion_fa_ex=[(r["word"], r["heard"], acc(r)) for r in motion][:10],
                    false_stop=dict(falsestop), stop_miss_top=miss.most_common(6), digit=conf)
    out["real_" + tag] = d
    S = rd("real/splice_%s.csv" % tag); s = {}
    for c in CONDS:
        sc = [r for r in S if r["cond"] == c]
        tgt = {"one": "rack_one", "two": "rack_two", "three": "rack_three"}
        ok = sum(r["accepted"].strip().endswith(tgt[r["word"]]) for r in sc)
        acc_n = sum(bool(r["accepted"].strip()) for r in sc)
        s[c] = dict(n=len(sc), correct=ok, wrong=acc_n - ok, rejected=len(sc) - acc_n,
                    wrong_ex=collections.Counter((r["word"], r["accepted"].strip()) for r in sc if r["accepted"].strip() and not r["accepted"].strip().endswith(tgt[r["word"]])).most_common())
    out["splice_" + tag] = s
for name, f in [("broad_before", "broad/results.csv"), ("broad_nato_after", "broad/results_nato_after.csv"), ("broad_orig_after", "broad/results_orig_phrases_after.csv")]:
    R = rd(f); o = collections.Counter(r["outcome"] for r in R)
    valid = [r for r in R if int(r["phrase_id"]) <= 17]; neg = [r for r in R if int(r["phrase_id"]) >= 18]
    stopp = [r for r in R if r["expected"].startswith("stop")]
    W = [r for r in R if r["outcome"] == "WRONG"]
    rackswap = [r for r in W if "rack" in r["got"] and "rack" in r["expected"] and r["got"].split()[-1] != r["expected"].split()[-1]]
    out[name] = dict(n=len(R), outcomes=dict(o), valid_acc=100*sum(r["outcome"]=="correct" for r in valid)/len(valid),
                     rej=100*sum(r["outcome"]=="rejected_ok" for r in neg)/len(neg),
                     stop=100*sum(r["outcome"]=="correct" for r in stopp)/len(stopp),
                     stop_by_phrase={p: "%d/%d" % (sum(r["outcome"]=="correct" for r in stopp if r["phrase"]==p), sum(r["phrase"]==p for r in stopp)) for p in sorted({r["phrase"] for r in stopp})},
                     wrong=len(W), rack_swaps=len(rackswap),
                     wrong_ex=collections.Counter((r["phrase"], r["got"].strip()) for r in W).most_common(12))
json.dump(out, open(os.path.join(V, "real", "summary.json"), "w"), indent=1, default=str)
# chart
fig, ax = plt.subplots(1, 3, figsize=(15, 4.5))
x = range(3)
for i, tag in enumerate(["before", "after"]):
    d = out["real_" + tag]
    ax[0].bar([j + i*0.38 for j in x], [100*d[c]["stop_hit"]/d[c]["stop_n"] for c in CONDS], 0.38, label=tag)
ax[0].set_xticks([j+0.19 for j in x]); ax[0].set_xticklabels(CONDS); ax[0].set_ylim(0, 100); ax[0].set_ylabel("%")
ax[0].set_title("Real 'stop' recall (400 speakers)\nmotion false accepts: 0 in every bar"); ax[0].legend()
for i, tag in enumerate(["before", "after"]):
    s = out["splice_" + tag]
    ax[1].bar([j + i*0.38 for j in x], [s[c]["wrong"] for c in CONDS], 0.38, label=tag, color=["tab:red", "tab:green"][i])
ax[1].set_xticks([j+0.19 for j in x]); ax[1].set_xticklabels(CONDS)
ax[1].set_title("Hybrid splice 'rack <real digit>'\nWRONG actions (450 per condition)"); ax[1].legend()
names = ["broad_before", "broad_nato_after", "broad_orig_after"]; lab = ["before\n(numbered racks)", "after\n(alpha/bravo/charlie)", "after, but user\nsays numbers"]
ax[2].bar(lab, [out[n]["wrong"] for n in names], color="tab:gray", label="all WRONG")
ax[2].bar(lab, [out[n]["rack_swaps"] for n in names], color="tab:red", label="wrong rack target")
for i, n in enumerate(names): ax[2].text(i, out[n]["wrong"] + 0.5, str(out[n]["wrong"]), ha="center")
ax[2].set_title("Synthetic broad suite WRONG actions\n(10,540 clips each)"); ax[2].legend()
plt.tight_layout(); plt.savefig(os.path.join(V, "real", "real_speech_chart.png"), dpi=110)
