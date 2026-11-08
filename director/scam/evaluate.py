"""Measure the HEURISTIC detector on the tiny hand-written set (director/scam/eval_set.py).

  python -m director.scam.evaluate [--json]

Positive = predicted "likely scam". "suspicious" is reported separately (a review flag). The numbers describe
these 30 fictional examples only; they are optimistic (same authors as the rules) and are not real-world accuracy.
"""

from __future__ import annotations

import argparse
import json
import sys

from .detector import NAME, detect
from .eval_set import EXAMPLES


def evaluate() -> dict:
    rows, tp, fp, fn, tn = [], 0, 0, 0, 0
    flagged = {"scam": 0, "legit": 0}
    for label, text in EXAMPLES:
        d = detect(text)
        pred = d.category == "likely scam"
        if d.category != "no warning signs found":
            flagged["scam" if label else "legit"] += 1
        tp += label and pred
        fp += (not label) and pred
        fn += label and not pred
        tn += (not label) and not pred
        rows.append({"label": label, "category": d.category, "indicators": d.ids, "text": text})
    n_pos, n_neg = sum(l for l, _ in EXAMPLES), sum(1 - l for l, _ in EXAMPLES)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {"detector": NAME, "dataset": "director/scam/eval_set.py (hand-written, fictional; NOT T-Mobile data)",
            "n": len(EXAMPLES), "scams": n_pos, "legitimate": n_neg,
            "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
            "precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision and recall else None,
            "accuracy": (tp + tn) / len(EXAMPLES),
            "any_warning_sign": {"scams_flagged": flagged["scam"], "legit_flagged": flagged["legit"]},
            "caveat": "tiny, hand-written by the rule authors: optimistic, not a real-world accuracy estimate",
            "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    r = evaluate()
    if args.json:
        print(json.dumps(r, indent=1))
        return 0
    c = r["confusion"]
    print(f"{r['detector']} on {r['dataset']}: {r['n']} messages ({r['scams']} scam, {r['legitimate']} legitimate)")
    print(f"  'likely scam' = positive:  TP {c['tp']}  FP {c['fp']}  FN {c['fn']}  TN {c['tn']}")
    print(f"  precision {r['precision']:.2f}  recall {r['recall']:.2f}  F1 {r['f1']:.2f}  accuracy {r['accuracy']:.2f}")
    print(f"  any warning sign: {r['any_warning_sign']['scams_flagged']}/{r['scams']} scams, "
          f"{r['any_warning_sign']['legit_flagged']}/{r['legitimate']} legitimate")
    print("  errors:")
    for row in r["rows"]:
        if (row["category"] == "likely scam") != bool(row["label"]):
            print(f"    {'MISSED' if row['label'] else 'FALSE ALARM'} [{row['category']}; {', '.join(row['indicators']) or '-'}] "
                  f"{row['text'][:90]}")
    print(f"  caveat: {r['caveat']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
