# TrajLab preliminary analysis and AI drawing pilot

Source: `trajlab_dataset_20261015_1552.zip`, exported October 15, 2026, at 20:52:24 UTC. This report describes the contents of that snapshot, not an independently verified participant study.

## Executive result

All 39 images already have human responses. This exercise adds **31 movement-path suggestions and 8 explicit WAIT annotations**, without changing the human records. Each movement path is attached to the original image, connects its current start and goal, and is stored as editable normalized points and cubic Bezier segments.

The geometric comparison covers the 31 movement scenes and 113 AI-human path pairs. Mean scene-balanced AI-human discrete Frechet distance is **4.12% of the image diagonal**, compared with **4.66%** for a straight start-to-goal line on the same scenes. The AI proposal is closer on **16 of 31** scenes, and farther on 15. This is a modest, mixed descriptive result, not evidence of consistent superiority.

**The human drawings were visible before the AI suggestions were authored. This is a reference-conditioned exploratory pilot, not a held-out VLM benchmark, not a full-ranking experiment, and not a demonstration of robot safety.**

## 1. What the export actually contains

| Item | Verified count |
|---|---:|
| Scenes with responses | 39 of 39 |
| Contributing participant codes | 19 |
| Participant accounts in the export | 24 |
| Human responses | 155 |
| Movement-path drawings | 145 |
| WAIT responses | 10 |
| Responses per scene | 3 to 6 |
| Survey/team response records | 147 / 8 |
| Existing VLM images / VLM ratings | 0 / 0 |

The contributing units are participant codes; the export alone does not establish that every account corresponds to a different person. There are no full rankings of a fixed candidate set in the supplied records. Responses contain one drawn route or a WAIT choice, optional norm tags, and a confidence rating. Scenario and train/test split fields are empty in all 39 scene records. Demographic questions were disabled in the supplied settings.

The draft statement that no real responses have been collected is therefore no longer an accurate description of this export. However, the team still needs to confirm which records were genuine study responses rather than tool tests; the export does not itself make that distinction. Do not equate these path drawings with the full preference rankings proposed in the paper.

## 2. How the new annotations were made

The assistant, GPT-6 Astra Pro, inspected each original image with its start/goal markers and the supplied human-path overlay. It selected its own image-coordinate waypoints rather than numerically fitting to the compiled human curve. A shape-preserving interpolator converted those waypoints to cubic Bezier segments; the exact waypoints and controls are in `data/ai_proposals.json` and the source script.

The intended preferences were to use visible walking corridors, avoid splitting groups and queues, avoid unnecessary grass or road excursions, and yield when the visible approach was blocked or depended too strongly on unseen motion. These are provisional interpretations, not observed ground truth. Several scenes start on grass or have goals near or behind people; those details are recorded in the per-scene review notes.

WAIT was used for scenes 04, 16, 17, 21, 23, 26, 28, and 35. Their overlays mark a stationary decision and contain no fabricated movement curve. Possible causes include exhibit or bunting barriers, an occupied stair landing, or crossing people blocking the approach. A delay tag denotes a possible waiting cost, not proof that waiting actually violates a norm in that scene.

No model was fine-tuned, no standalone API experiment was run, and no calibrated probability was obtained. The qualitative confidence labels are only authoring notes. The scripts make this fixed drawing exercise reproducible; they do not implement an autonomous route generator for new images.

## 3. Geometric measurement

The exported normalized coordinates are first scaled into aspect-ratio-correct image-diagonal units:

```text
p(x, y) = (W*x, H*y) / sqrt(W^2 + H^2)
D(P, Q) = discrete_Frechet(p(P), p(Q))
```

The stored 64-point human curves are used as supplied. Each AI Bezier curve is densely sampled and then resampled to 64 approximately equal pixel-arclength points. The straight-line reference is sampled at 64 points between the exact current start and goal. It is a geometric sanity check, **not a collision-free or socially valid shortest-path planner**.

For every movement scene, distance is computed to each non-admin-excluded human movement path and then averaged within the scene. The reported primary aggregate is the mean of these 31 scene means, so scenes with six responses do not outweigh scenes with three. Human WAIT decisions and AI WAIT outputs receive no geometric distance; they are never represented by a zero-error curve.

The primary calculation retains the two export-flagged outliers, the five compiled-curve trimming flags, and the three endpoint-mismatched paths. None was silently discarded. Separate sensitivity results follow below. Recomputed human-human distances under the export's outlier filter differ from its stored scene means by no more than 0.00000416 diagonal units, consistent with rounding of the saved coordinates.

| Comparison | Scene-balanced distance, % of image diagonal |
|---|---:|
| AI proposal to all human paths, same 31 scenes | 4.12% |
| Straight-line reference to all human paths, same 31 scenes | 4.66% |
| Human to human, same 31 scenes | 5.73% |
| AI proposal to nearest human path, 31 scenes | 1.51% |
| Human to human, all 39 scenes | 5.90% |

The difference from the straight line is about **11.6% relative reduction in this geometric distance**, but the proposals only improve 16 of 31 individual scenes. The AI-human value being below human-human disagreement does not establish that the assistant is better than a human: the assistant had access to the answers, and an aggregate-compatible route need not represent a new person's independent choice.

The export's transformed similarity, `1 / (1 + D / 0.10)`, averages 0.771 for the AI-human pairs after scene balancing. This is a distance-derived index, **not 77.1% accuracy or acceptability**.

### Tolerance sensitivity

The following is the mean within-scene fraction of AI-human or straight-line-human pairs inside the indicated distance tolerance. It is not a new clustering analysis.

| Distance tolerance | AI-human pairs within tolerance | Straight-line-human pairs within tolerance |
|---|---:|---:|
| 2.5% | 54.8% | 43.2% |
| 5% | 82.3% | 61.7% |
| 7.5% | 88.2% | 79.3% |
| 10% | 90.6% | 88.0% |

At the export's 0.10-diagonal tolerance, each of the 31 AI movement curves is nearest to a modal exported route medoid and is within that tolerance. This must not be headlined as 100% navigation accuracy: the reference drawings were already visible, there can be tied modes, and 0.10 of the diagonal is about **147 pixels** in a 1280-by-720 image. The stricter tolerance results show how strongly geometric agreement depends on the chosen threshold.

### Waiting must be reported separately

Across all 155 human responses, the AI path-versus-WAIT decision matches 120 responses, or **77.4%**. Always choosing a path would match **93.5%**, because only 10 of the 155 human responses are WAIT. The AI matches the majority action in 31 of 39 scenes; the human majority action is movement in every scene.

| Human response | AI path | AI WAIT |
|---|---:|---:|
| Path | 113 | 32 |
| WAIT | 3 | 7 |

The additional WAIT decisions are not established improvements. They reflect this pilot's cautious interpretation of visible blockage and missing timing information. Independent human judgments, an explicit waiting horizon, and temporal context are needed to evaluate whether those choices are appropriate. No waiting penalty was converted into an arbitrary geometric distance.

## 4. Human disagreement and norm tags

The export reports **10 scenes with two geometric route modes**, **9 scenes with at least one WAIT response**, and **17 scenes with more than one substantial route-or-WAIT choice** under its current settings. Four scenes carry its ambiguity flag: scene 07 (SCAND frame at 0m1s), scene 16 (`fri6`), scene 26 (`15h10m59s609`), and scene 38 (`15h58m44s404`). These are descriptive summaries of the exported grouping, not independently validated psychological categories.

Scene 07 illustrates why one distance to an average path can be misleading. Its four human paths divide between two routes. The proposed right-side route is 1.11% of the image diagonal from the nearest human path but averages 11.45% from all four. A valid minority or tied route can have a large average distance simply because the other route goes around the opposite side. Both the alternatives and their shares should remain visible; a mean curve should not be assumed executable.

Personal-space tags appear on **75/155 responses (48.4%)**, group/activity tags on **39/155 (25.2%)**, delay tags on **15/155 (9.7%)**, and terrain tags on **14/155 (9.0%)**. Participants could choose multiple tags, so these percentages need not sum to 100%. Forty-three responses have no selected norm tags. An empty tag set is not proof that the route is norm-compliant; the tags were optional and not exhaustively adjudicated.

These observations support analyzing contextual trade-offs and alternative choices, but they do not establish that every scene is over-constrained. The open approach in `fri5`, for example, is better treated as a possible control scene unless the team identifies an actual unavoidable norm conflict. A single preferred drawing cannot prove that every physically safe alternative breaks a norm.

## 5. Data-quality findings that matter for the paper

### Current-goal mismatch

Three movement drawings end more than 1% of the image diagonal from the scene's current goal. In `fri3`, response S0003 ends about **232 pixels (15.82% of the diagonal)** from the current goal. Two `fri5` responses miss the current goal by approximately 26 and 19 pixels. These could reflect edited goal markers or incomplete drawing; the cause is not established from the export.

The `fri3` mismatch belongs to its one-member alternative route. Removing that record leaves only one populated exported route label in that scene, so the apparent second mode must not automatically be interpreted as social preference disagreement. Repair or version the goal/response relationship before making a final multimodality claim.

When the three endpoint-mismatched drawings are omitted, mean AI-human distance changes from **4.12% to 4.00%**, and the straight-line value changes from **4.66% to 4.56%** on the same 31 scenes. Excluding only the two export-flagged outliers gives **3.81%** for AI-human distance. Applying both filters gives **3.69%**. These are sensitivity analyses, not replacements for the primary figures.

### Repeated stored geometry

There are 10 within-scene groups of exactly identical stored curves, containing 23 drawings in total (13 repeated curves beyond one representative per group). This is not evidence of duplicate participants, copying, or misconduct. A default straight start-to-goal curve can be chosen independently. Check the interface's initial curve and edit history before treating identical geometry as strong independent agreement or removing it.

### Timing, physical feasibility, and scene selection

Still images do not establish metric clearance, ground-plane geometry, speed, pedestrian intent, future occupancy, vehicle movement, or barrier permission. Some goals lie at visibly occupied or occluded regions. Several AI curves consequently include a review note requiring the approach to clear before any execution. All records explicitly set `physically_validated` to false.

The current paper's statement that every candidate has been physically checked should not be applied to this pilot or assumed for the supplied freehand drawings. Use the narrower description **visually proposed image-space routes requiring independent feasibility checks**. Do not command Spot from these coordinates.

Scenario and split fields are empty. The filenames and images include multiple related event/recording frames, so a future held-out split should keep related clips or recording episodes together rather than randomly separating near-duplicate frames. Demographic familiarity, approval status, and source-video permissions cannot be established by this analysis.

## 6. What is and is not supported now

The current export supports a descriptive annotation audit, within-scene route diversity, WAIT frequency, norm-tag distributions, and this explicitly reference-conditioned geometric comparison. It also supplies useful examples for improving the survey and checking the rendering/evaluation pipeline.

It does **not** yet support full-ranking agreement, preference-learning improvements, confidence calibration against human disagreement, VLM acceptability or safety ratings, inter-rater reliability for the VLM questionnaire, collision-free execution, context-conditioned weight learning, or performance on held-out images. There are no VLM ratings in the original export, and a single interacting assistant session cannot isolate an in-context-learning benefit from familiarity with the very drawings being scored.

For a next evaluation, freeze scene geometry and candidate sets, resolve the three endpoint discrepancies, specify the waiting duration and allowed off-path behavior, and group train/test splits by recording episode. Provide only training examples to a fixed model prompt and obtain fresh blinded human ratings on the generated candidates. Keep ordinary navigable scenes as controls and independently screen the over-constrained scenes rather than assuming the label from crowd density alone.

## 7. Files and paper use

`trajlab_pilot_results.tex` provides insertable subsections and a table for the existing IEEE paper. It deliberately describes the exercise as a reference-conditioned pilot. `data/scene_metrics.csv` contains every scene, including blank geometric values for WAIT; the pairwise CSVs and scripts allow recalculation.

Use `images/<folder>/ai_path.jpg` as the AI-only visual artifact. The comparison and review images reveal human answers and are not suitable as blinded test inputs. Do not import AI records as human participants or replace the original export. The interchange JSON preserves scene IDs and editable points, but an exact TrajLab import/API schema was not present and has not been verified.

Names, email addresses, account records, and participant free-text comments are not included in the derivative package. Participant codes are pseudonyms, and the images still contain recognizable people. Confirm the study's permissions and approvals before distributing the photographs or results beyond the group.
