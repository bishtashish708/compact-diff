"""Score compact-diff verdicts against blind gold labels. Prints aggregates only.

    python3 launch/eval/score.py PACKETS_DIR LABELS_DIR > results.json

Labels: LABELS_DIR/<packet id>.json = {"id", "labels": [{"id", "label", "is_real_item"}]}.
Packet "group"/"steer" fields are optional (used for the historical/fork breakdowns).
"""
import json, os, glob, sys, itertools
from collections import Counter, defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from compact_diff import extract, match
PACKETS, LABELS = (sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else sys.exit("usage: score.py PACKETS_DIR LABELS_DIR")
C = ("kept", "partial", "dropped")

def load():
    rows = []
    for full in sorted(glob.glob(os.path.join(PACKETS, "*.full.json"))):
        p = json.load(open(full))
        lf = os.path.join(LABELS, p["id"] + ".json")
        if not os.path.exists(lf): continue
        gold = {l["id"]: l for l in json.load(open(lf))["labels"]}
        summ = match.Summary(p["summary"])
        for d in p["items"]:
            g = gold.get(d["id"])
            if not g: continue
            it = extract.Item.from_dict(d)
            rows.append({"cid": p["id"], "group": p["group"], "steer": p.get("steer"), "kind": it.kind, "item": it,
                         "gold": g["label"], "real": g.get("is_real_item", True), "summ": summ})
    return rows

def verdict(r, kept_at, partial_at):
    match.KEPT_AT, match.PARTIAL_AT = kept_at, partial_at
    return match.judge(r["item"], r["summ"]).status

def metrics(rows, kept_at=0.7, partial_at=0.4):
    cm = Counter(); 
    for r in rows: cm[(r["gold"], verdict(r, kept_at, partial_at))] += 1
    out = {}
    for c in C:
        tp = cm[(c, c)]; pp = sum(cm[(g, c)] for g in C); ap = sum(cm[(c, p)] for p in C)
        out[c] = {"precision": tp / pp if pp else None, "recall": tp / ap if ap else None, "support": ap}
    acc = sum(cm[(c, c)] for c in C) / max(1, sum(cm.values()))
    # binary "lost" view: gold lost = partial|dropped ; tool lost = partial|dropped
    lost = lambda s: s != "kept"
    tp = sum(v for (g, p), v in cm.items() if lost(g) and lost(p)); pp = sum(v for (g, p), v in cm.items() if lost(p)); ap = sum(v for (g, p), v in cm.items() if lost(g))
    f1s = [2 * m["precision"] * m["recall"] / (m["precision"] + m["recall"]) for m in out.values() if m["precision"] and m["recall"]]
    return {"n": sum(cm.values()), "accuracy": acc, "macro_f1": sum(f1s) / 3, "per_class": out,
            "lost_precision": tp / pp if pp else None, "lost_recall": tp / ap if ap else None, "cm": {f"{g}->{p}": v for (g, p), v in sorted(cm.items())}}

GRID = [(k / 100, p / 100) for k in range(40, 95, 5) for p in range(10, 75, 5) if p < k]

def best(rows):
    return max(GRID, key=lambda kp: (metrics(rows, *kp)["macro_f1"], -abs(kp[0] - 0.7)))

def main():
    rows = load(); real = [r for r in rows if r["real"]]
    res = {"compactions": len({r["cid"] for r in rows}), "items_labelled": len(rows),
           "extraction_precision": {k: (sum(r["real"] for r in rows if r["kind"] == k), sum(1 for r in rows if r["kind"] == k)) for k in sorted({r["kind"] for r in rows})},
           "default": metrics(real)}
    # leave-one-compaction-out threshold tuning
    cids = sorted({r["cid"] for r in real}); preds = []
    picks = []
    for c in cids:
        train = [r for r in real if r["cid"] != c]; kp = best(train); picks.append(kp)
        preds += [(r, kp) for r in real if r["cid"] == c]
    cm_rows = []
    for r, kp in preds: r2 = dict(r); r2["kp"] = kp; cm_rows.append(r2)
    # evaluate LOCO: compute metrics per held-out with its kp
    cm = Counter()
    for r in cm_rows: cm[(r["gold"], verdict(r, *r["kp"]))] += 1
    res["loco_accuracy"] = sum(cm[(c, c)] for c in C) / max(1, sum(cm.values()))
    res["loco_picks"] = Counter(picks).most_common(3)
    kp_all = best(real); res["tuned_all"] = {"thresholds": kp_all, **metrics(real, *kp_all)}
    res["per_kind_default"] = {k: metrics([r for r in real if r["kind"] == k]) for k in sorted({r["kind"] for r in real})}
    # steering comparison on fork pairs (gold labels)
    for steer in (True, False):
        g = [r for r in real if r["group"] == "fork" and r["steer"] is steer]
        if g:
            res[f"steer_{'on' if steer else 'off'}_gold"] = dict(Counter(r["gold"] for r in g))
            res[f"steer_{'on' if steer else 'off'}_gold_by_kind"] = {k: dict(Counter(r["gold"] for r in g if r["kind"] == k)) for k in sorted({r["kind"] for r in g})}
    hist = [r for r in real if r["group"] == "historical"]
    res["historical_unsteered_gold_by_kind"] = {k: dict(Counter(r["gold"] for r in hist if r["kind"] == k)) for k in sorted({r["kind"] for r in hist})}
    match.KEPT_AT, match.PARTIAL_AT = 0.7, 0.4
    print(json.dumps(res, indent=1, default=str))

if __name__ == "__main__": main()
