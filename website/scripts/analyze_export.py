#!/usr/bin/env python3
"""
Re-run the path analysis on a downloaded dataset, e.g. with a different same-route threshold,
only the research team's or only the survey's annotations, without touching the live site.

    python scripts/analyze_export.py trajlab_dataset.zip
    python scripts/analyze_export.py trajlab_dataset.zip --threshold 0.08 --role survey --out results/

Writes image_summary.csv (agreement per training image) and model_paths.csv (path agreement
per model, for VLM outputs with coordinates) to --out, and prints a short report.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import analysis as A  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", help="zip downloaded from Admin > Data")
    ap.add_argument("--threshold", type=float, help="same-route threshold, fraction of the image diagonal")
    ap.add_argument("--ambiguity", type=float, help="ambiguous if the top choice share is below this")
    ap.add_argument("--method", choices=["mean", "medoid"], help="how to compile one trajectory per image")
    ap.add_argument("--keep-outliers", action="store_true", help="count outlier paths in the vote and compiled path")
    ap.add_argument("--role", choices=["team", "survey"], help="only use annotations from this role")
    ap.add_argument("--split", help="only images in this split (e.g. test)")
    ap.add_argument("--include-excluded", action="store_true", help="also use annotations excluded in the admin")
    ap.add_argument("--out", default=".", help="output folder")
    args = ap.parse_args()

    with zipfile.ZipFile(args.export) as z:
        data = json.loads(z.read(next(n for n in z.namelist() if n.endswith("dataset.json"))))

    params = {}
    if args.threshold is not None:
        params["cluster_threshold"] = args.threshold
    if args.ambiguity is not None:
        params["ambiguity_threshold"] = args.ambiguity
    if args.method:
        params["compiled_method"] = args.method
    if args.keep_outliers:
        params["exclude_outliers"] = False

    results, rows = [], []
    for img in data["images"]:
        if args.split and img.get("split") != args.split:
            continue
        humans = [{"id": a["id"], "participant_id": a["code"], "decision": a["decision"], "points": a["points"],
                   "segments": a["segments"], "style": a["style"], "norms": a["norms"]}
                  for a in data["annotations"] if a["image_id"] == img["id"]
                  and (args.include_excluded or not a["excluded"]) and (not args.role or a["role"] == args.role)]
        cands = [{"id": c["id"], "model": c["model"], "decision": c["decision"], "kind": "waypoints",
                  "points": c["points"], "segments": c["segments"], "consensus_override": bool(c["consensus_override"])}
                 for c in data["vlm_images"] if c["image_id"] == img["id"] and (c["points"] or c["decision"] == "wait")]
        res = A.analyze_scene(img, humans, cands, params)
        results.append({"scene_id": img["id"], "analysis": res})
        rows.append({"image_id": img["id"], "title": img["title"], "scenario": img.get("scenario"),
                     "split": img.get("split"), **res["summary"]})
    paths = A.model_path_summary(results)

    os.makedirs(args.out, exist_ok=True)
    for fname, table in (("image_summary.csv", rows),
                         ("model_paths.csv", [{"model": m, **v} for m, v in paths.items()])):
        if not table:
            continue
        with open(os.path.join(args.out, fname), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(table[0].keys()))
            w.writeheader()
            for r in table:
                w.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in r.items()})

    print(f"{len(rows)} images analysed, {sum(r['ambiguous'] for r in rows)} ambiguous, "
          f"{sum(r['n_outliers'] for r in rows)} outlier paths")
    for m, v in sorted(paths.items()):
        ca, fr = v["choice_agreement"], v["frechet_to_compiled"]
        print(f"  {m:<28} same route as people {'–' if ca is None else f'{ca:.0%}':>5}   "
              f"Frechet to compiled path {'–' if fr is None else f'{fr:.1%}':>6}   paths {v['n_paths']}")
    print(f"wrote {args.out}/image_summary.csv" + (f" and {args.out}/model_paths.csv" if paths else ""))


if __name__ == "__main__":
    main()
