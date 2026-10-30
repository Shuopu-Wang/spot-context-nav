# TrajLab: reference-conditioned AI drawing pilot

**Start with `report.html`.** It includes the results and a visual review of all 39 scenes. Its thumbnail images are embedded; full-resolution links work when the complete ZIP has been extracted.

This package adds 31 image-space movement suggestions and 8 explicit WAIT annotations. It does not overwrite the original export, add human responses, or claim robot safety. The assistant viewed the human drawings before proposing routes, so this is not a held-out experiment.

## Files

- `report.html`: illustrated report, all 39 decisions, rationales, caveats, and quantitative results.
- `report.md`: the full report in editable plain text.
- `trajlab_pilot_results.tex`: insertable LaTeX subsections and results table for the existing IEEE paper.
- `images/<original-folder>/ai_path.jpg`: AI-only overlay at the original image dimensions, without human paths or agreement statistics. WAIT records have no movement curve.
- `images/<original-folder>/comparison.jpg`: AI overlay plus human drawings. Cyan is AI; amber is human; dashed amber indicates a supplied outlier flag. No paths were hidden for endpoint mismatch.
- `images/<original-folder>/review.jpg`: comparison with per-scene results and review notes.
- `images/<original-folder>/ai_overlay.svg`: transparent editable vector overlay.
- `data/ai_proposals.json`: exact normalized waypoints, cubic Bezier controls, decisions, possible norm costs, and notes. This is a documented interchange file, **not a verified TrajLab importer schema**.
- `data/ai_proposals_with_samples.json`: the same records with the 64-point curves used for measurement.
- `data/scene_metrics.csv`: one row for each of the 39 scenes. Geometric fields are blank for WAIT, not zero.
- `data/ai_human_pairs.csv`: 113 AI-human geometric comparisons on the 31 movement scenes, including straight-line references and audit flags.
- `data/human_human_pairs.csv`: all 215 within-scene human path pairs.
- `data/data_quality_flags.csv`: three current-goal endpoint mismatches above 1% of the image diagonal.
- `data/identical_geometry_groups.csv`: exact stored-curve repeats; these are not evidence of duplicate people or misconduct.
- `data/norm_frequency.csv`, `data/threshold_sensitivity.csv`, `data/summary.json`: supporting numbers.
- `data/human_reference.json`: pseudonymous reference geometry sufficient to reproduce the metrics. Names, emails, account records, and free-text comments are omitted.
- `figures/`: all-scene contact sheets, an example figure, and diagnostic plots.
- `code/`: scripts used to encode, measure, render, and document the pilot.

## Reproduce the metrics

From the extracted package directory:

```sh
python -m pip install -r requirements.txt
python code/analyze.py --root .
python code/plot_results.py
```

To reconstruct the frozen waypoint proposals and render against the original photos, extract the original TrajLab ZIP into a separate folder, then run:

```sh
python code/create_proposals.py --source /path/to/original_export --root .
python code/analyze.py --root .
python code/render.py --source /path/to/original_export --root .
```

The waypoint script encodes this single assistant-authored pilot. It does not call a VLM API, infer new paths, or implement an autonomous navigation policy. The path controls use shape-preserving interpolation; the underlying choices are visible in the source. The exact records, not a hidden model seed, make the geometric analysis reproducible.

## Interpretation and restrictions

All geometry is in the camera image, not meters. A small Frechet distance is visual similarity, not probability of being acceptable, physically safe, or collision-free. The straight line is a geometric sanity check, not a collision-aware shortest-path planner. WAIT decisions are evaluated separately from movement geometry.

The supplied default 0.10-diagonal grouping tolerance is approximately 147 pixels for a 1280-by-720 image. Nearest-medoid agreement at this tolerance should not be described as navigation accuracy. The primary calculation retains all non-admin-excluded human paths, including the two export-flagged outliers and three endpoint-mismatched paths. Sensitivity results are explicitly identified.

Do not merge AI annotations into the human response table. Do not show `comparison.jpg`, `review.jpg`, or existing participant choices to raters in an intended blinded test. The AI-only overlays still need independent acceptability ratings and technical safety review before being considered for execution.

The original export contained participant identifiers. They are not copied into this package. Participant codes remain pseudonymous, and photographs still contain recognizable people; this package is **not** a fully de-identified dataset. Confirm your study's permissions and approvals before circulating it beyond the research group.
