"""Build an editable report and a self-contained illustrated HTML review."""
import base64,csv,hashlib,html,io,json,platform,re
from pathlib import Path
from PIL import Image,ImageOps
import importlib.metadata

R=Path(__file__).resolve().parents[1]
S=json.loads((R/'data/summary.json').read_text())
P=json.loads((R/'data/ai_proposals_with_samples.json').read_text())['proposals']
M=list(csv.DictReader((R/'data/scene_metrics.csv').open()))
T=list(csv.DictReader((R/'data/threshold_sensitivity.csv').open()))

def pc(x):return f'{100*x:.2f}%'

def markdown_inline(t):
    t=html.escape(t)
    t=re.sub(r'\*\*(.+?)\*\*',r'<strong>\1</strong>',t)
    t=re.sub(r'`([^`]+)`',r'<code>\1</code>',t)
    t=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',r'<a href="\2">\1</a>',t)
    return t

def simple_markdown(text):
    lines=text.splitlines();out=[];i=0
    while i<len(lines):
        line=lines[i]
        if not line.strip():i+=1;continue
        if line.startswith('```'):
            i+=1;buff=[]
            while i<len(lines) and not lines[i].startswith('```'):buff.append(lines[i]);i+=1
            out.append('<pre><code>'+html.escape('\n'.join(buff))+'</code></pre>');i+=1;continue
        if line.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].startswith('|'):
                cells=[x.strip() for x in lines[i].strip('|').split('|')]
                if not all(re.fullmatch(r'[-: ]+',x or '-') for x in cells):rows.append(cells)
                i+=1
            out.append('<div class="table-wrap"><table>')
            for j,cells in enumerate(rows):
                tag='th' if j==0 else 'td';out.append('<tr>'+''.join(f'<{tag}>'+markdown_inline(x)+f'</{tag}>' for x in cells)+'</tr>')
            out.append('</table></div>');continue
        if line.startswith('#'):
            level=len(line)-len(line.lstrip('#'));out.append(f'<h{level}>'+markdown_inline(line[level:].strip())+f'</h{level}>');i+=1;continue
        paragraph=[line];i+=1
        while i<len(lines) and lines[i].strip() and not lines[i].startswith(('#','|','```')):paragraph.append(lines[i]);i+=1
        out.append('<p>'+markdown_inline(' '.join(paragraph))+'</p>')
    return '\n'.join(out)

threshold_rows='\n'.join(f"| {100*float(r['threshold_diagonal']):g}% | {100*float(r['ai_human_within_threshold_macro_31_scenes']):.1f}% | {100*float(r['straight_human_within_threshold_macro_same_31_scenes']):.1f}% |" for r in T)
report=f'''# TrajLab preliminary analysis and AI drawing pilot

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
{threshold_rows}

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
'''
(R/'report.md').write_text(report)

latex=r'''% Insert these subsections into Preliminary Results, replacing outdated claims.
% Uses packages already present in the supplied IEEE document.
% All values refer to trajlab_dataset_20261015_1552.zip (2026-10-15).
\subsection{Dataset Audit}
The October 15 export contains 39 scenes and 155 response records from 19
contributing participant codes: 145 drawn paths and 10 wait choices, with
three to six responses per scene. These are single drawn-path or wait
choices rather than complete rankings of a fixed candidate set. The export
contains no VLM outputs or VLM questionnaire ratings. We therefore treat
these data as an initial annotation pilot; the team must still verify which
records are study responses rather than interface tests.

Under the export's current grouping settings, 10 scenes have two geometric
route modes, nine include a wait response, and 17 contain more than one
substantial route-or-wait choice. Personal-space and group/activity tags
occur in 75 and 39 responses, respectively. These counts indicate diversity
in the recorded choices, but do not establish that every scene is
over-constrained or that every alternative route is physically feasible.

\subsection{Reference-Conditioned Drawing Pilot}
We additionally conducted an exploratory drawing exercise using GPT-6
Astra Pro in an interactive session. The assistant inspected the scene
images and existing human overlays, then authored new image-coordinate
waypoints. Shape-preserving interpolation converted the waypoints to
cubic B\'ezier curves. The exercise produced 31 movement suggestions and
eight explicit wait annotations. Because the human drawings were visible,
this is a reference-conditioned pilot, not a held-out evaluation of our
proposed in-context ranking method.

For geometric comparison, coordinates were scaled as
\begin{equation}
\phi(x,y)=\frac{(Wx,Hy)}{\sqrt{W^2+H^2}},
\end{equation}
and discrete Fr\'echet distances were computed using 64-point curves.
Distances were first averaged over human movement paths within each scene,
then averaged equally across the 31 scenes with an AI movement suggestion.
Wait outputs were not assigned geometric distances. The primary analysis
retained all non-administratively-excluded paths, including flagged
outliers and endpoint discrepancies.

\begin{table}[t]
\caption{Exploratory geometric comparison on the same 31 movement scenes.
Distances are percentages of the image diagonal; lower values indicate
closer image-space geometry, not greater safety or acceptability.}
\label{tab:reference_conditioned_pilot}
\centering
\begin{tabular}{@{}p{0.75\columnwidth}r@{}}
\toprule
\textbf{Comparison} & \textbf{Distance} \\
\midrule
AI suggestion to all human paths & 4.12\% \\
Straight start-to-goal line to human paths & 4.66\% \\
Human-to-human path pairs & 5.73\% \\
AI suggestion to nearest human path & 1.51\% \\
\bottomrule
\end{tabular}
\end{table}

The AI suggestions were closer to human drawings than the straight-line
reference in 16 of 31 scenes, with a scene-averaged distance of 4.12\%
versus 4.66\% of the image diagonal. The straight line is only a geometric
sanity check, not a collision-aware shortest-path baseline. Path-versus-wait
agreement was 120/155 responses (77.4\%); always choosing movement would
score 145/155 (93.5\%), illustrating the imbalance of the recorded choices.
The extra wait decisions have not been independently judged preferable.

\subsection{Data Quality and Limitations}
Three human drawings end more than 1\% of the image diagonal from their
scene's current goal. One \texttt{fri3} response differs by about 232 pixels
and accounts for that scene's one-member alternative route. Omitting the
three endpoint-mismatched drawings changes mean AI--human distance from
4.12\% to 4.00\%. The cause of the discrepancies must be resolved before
interpreting all geometric modes as social disagreement.

The exercise measures resemblance to visible references, not generalization,
ranking accuracy, calibrated confidence, or physical safety. No depth,
clearance, dynamic collision checking, or robot execution was performed.
The new suggestions should therefore be treated as image-space candidates
requiring independent feasibility and acceptability review. A subsequent
experiment will require frozen scene geometry, explicit waiting semantics,
recording-level train/test separation, and blinded human evaluation.
'''
(R/'trajlab_pilot_results.tex').write_text(latex)

def embed_image(path,maxsize=(620,400),quality=80):
    im=Image.open(path).convert('RGB');im=ImageOps.contain(im,maxsize);buf=io.BytesIO();im.save(buf,format='JPEG',quality=quality)
    return 'data:image/jpeg;base64,'+base64.b64encode(buf.getvalue()).decode()

cards=[]
for p,m in zip(P,M):
    assert p['image_id']==m['image_id']
    directory='images/'+p['folder']
    image=embed_image(R/directory/'comparison.jpg')
    metric='N/A - stationary decision' if p['decision']=='wait' else f"{100*float(m['ai_mean_frechet']):.2f}% of diagonal"
    cards.append(f'''<article class="card" data-kind="{p['decision']}" data-search="{html.escape((str(p['index'])+' '+p['image_title']+' '+p['folder']).lower())}">
    <a href="{directory}/review.jpg"><img src="{image}" alt="Scene {p['index']:02d}: AI {p['decision']} and existing human paths"></a>
    <div class="cardbody"><span class="badge {p['decision']}">{p['index']:02d} / {p['decision'].upper()}</span>
    <h3>{html.escape(p['image_title'])}</h3><p><strong>AI-human distance:</strong> {metric}<br>Humans: {m['n_human_paths']} paths, {m['n_human_wait']} waits.</p>
    <p>{html.escape(p['rationale'])}</p><details><summary>Norm costs, uncertainty, and files</summary>
    <p><strong>Possible costs:</strong> {html.escape(', '.join(p['norms']) or 'No obvious immediate norm cost identified')}<br><strong>Uncalibrated confidence:</strong> {p['confidence_label']}</p>
    <p><strong>Required review:</strong> {html.escape(p['required_review'])}</p>
    <p><a href="{directory}/ai_path.jpg">AI-only image</a> &middot; <a href="{directory}/comparison.jpg">Comparison</a> &middot; <a href="{directory}/ai_overlay.svg">Vector overlay</a></p></details></div></article>''')
plot1=embed_image(R/'figures/geometry_comparison.png',(950,850),90)
plot2=embed_image(R/'figures/threshold_sensitivity.png',(950,650),90)
css='''*{box-sizing:border-box}body{margin:0;background:#f5f7fa;color:#172a3a;font:17px/1.65 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}header{background:#122333;color:#fff;padding:52px max(24px,calc((100vw - 1160px)/2))}header p{max-width:880px;color:#d6e4ed}.eyebrow{text-transform:uppercase;letter-spacing:.15em;font-size:13px;font-weight:700}.hero{font-size:clamp(30px,4vw,48px);line-height:1.15;max-width:1000px;margin:12px 0 20px}main{max-width:1220px;margin:auto;padding:30px}.notice{border-left:5px solid #c58118;background:#fff5e3;padding:18px 23px;border-radius:5px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:15px;margin:26px 0}.stat{background:#fff;padding:18px;border-radius:10px;border:1px solid #dbe2e8}.stat strong{display:block;font-size:32px;line-height:1.2}.report{background:#fff;padding:30px 38px;border-radius:12px;margin:28px 0}.report h1{display:none}h2{font-size:26px;line-height:1.3;margin-top:38px;padding-top:12px;border-top:1px solid #dbe2e8}h3{font-size:20px;line-height:1.35}a{color:#08668c}code{background:#edf2f5;border-radius:3px;padding:2px 5px;font-size:.91em;overflow-wrap:anywhere}pre{background:#edf2f5;padding:18px;overflow:auto}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;margin:20px 0;font-size:16px}td,th{text-align:left;padding:10px 12px;border-bottom:1px solid #dfe5eb}th{background:#edf2f5}tr:nth-child(even){background:#f8fafb}.charts{display:flex;gap:20px;flex-wrap:wrap}.charts img{width:min(100%,540px);height:auto;object-fit:contain}.controls{display:flex;gap:12px;margin:22px 0;flex-wrap:wrap}input,select{padding:12px;font:inherit;border:1px solid #bccad5;border-radius:6px}input{flex:1;min-width:230px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}.card{background:#fff;border:1px solid #dbe2e8;border-radius:12px;overflow:hidden}.card img{width:100%;display:block;background:#edf2f5}.cardbody{padding:20px}.card h3{font-size:18px;overflow-wrap:anywhere}.card p{font-size:15px;line-height:1.55}.badge{font-size:13px;font-weight:800;letter-spacing:.08em;background:#e5f7fa;padding:5px 9px;border-radius:4px}.badge.wait{background:#fff0d8}details{border-top:1px solid #e3e9ef;padding-top:10px}summary{cursor:pointer;font-size:15px;font-weight:650}footer{padding:35px 0;color:#526575;font-size:14px}@media(max-width:700px){main{padding:16px}.report{padding:20px}.grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}}@media print{.controls{display:none}.card{break-inside:avoid}.report{padding:0}header{padding:20px}}'''
page=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TrajLab preliminary results and AI annotations</title><style>{css}</style></head><body>
<header><div class="eyebrow">TrajLab / October 15, 2026 snapshot</div><div class="hero">New annotations.<br>Measured resemblance.<br>Explicit limitations.</div><p>A reference-conditioned drawing pilot on all 39 scenes, with editable trajectories, human comparisons, and an audit of what the current export can support.</p></header>
<main><div class="notice"><strong>Not a held-out evaluation.</strong> Human drawings were visible before the AI proposals were authored. These image-space suggestions are not physically validated robot trajectories.</div>
<div class="stats"><div class="stat"><strong>39</strong>Scenes reviewed</div><div class="stat"><strong>155</strong>Human response records</div><div class="stat"><strong>31 + 8</strong>New paths + WAIT choices</div><div class="stat"><strong>4.12%</strong>Mean AI-human distance</div></div>
<p><a href="#gallery">Browse all scene annotations</a> &middot; <a href="trajlab_pilot_results.tex">LaTeX results</a> &middot; <a href="data/scene_metrics.csv">Per-scene CSV</a> &middot; <a href="data/ai_proposals.json">Editable proposals</a></p>
<div class="report">{simple_markdown(report)}<h2>Diagnostic figures</h2><div class="charts"><img src="{plot1}" alt="AI versus straight-line distance by scene"><img src="{plot2}" alt="Agreement as a function of distance tolerance"></div></div>
<section id="gallery"><h2>All 39 scene annotations</h2><p>Cyan is AI; amber is human. Click an image for the full-resolution review after extracting the complete package. Thumbnails are embedded in this HTML file. Search accepts scene numbers, titles, or folder suffixes.</p>
<div class="controls"><input id="search" placeholder="Search: fri2, 25, da02ef..."><select id="kind"><option value="all">All decisions</option><option value="path">Movement paths</option><option value="wait">WAIT annotations</option></select></div><div class="grid">{''.join(cards)}</div></section>
<footer>Participant names, emails, account records, and free-text comments have been omitted. Codes are pseudonyms; photographs are not fully de-identified. Preserve the original export and keep AI records separate from human responses.</footer></main>
<script>function filter(){{const q=document.getElementById('search').value.toLowerCase().trim(),k=document.getElementById('kind').value;document.querySelectorAll('.card').forEach(c=>{{c.style.display=(!q||c.dataset.search.includes(q))&&(k==='all'||c.dataset.kind===k)?'':'none'}})}}document.getElementById('search').addEventListener('input',filter);document.getElementById('kind').addEventListener('change',filter);</script></body></html>'''
(R/'report.html').write_text(page)
(R/'index.html').write_text('<!doctype html><html><head><meta http-equiv="refresh" content="0;url=report.html"><title>TrajLab</title></head><body><a href="report.html">Open the illustrated report</a></body></html>')
env={'python':platform.python_version(),'packages':{k:importlib.metadata.version(k) for k in ['numpy','scipy','Pillow','matplotlib']},'model_label':'GPT-6 Astra Pro','protocol':'interactive reference-conditioned proposal authoring; no standalone model API request, calibrated probabilities, fine-tuning, or independent held-out predictions','source_export':'2026-10-15T20:52:24+00:00','ai_proposals_sha256':S['ai_proposals_sha256']}
(R/'data/provenance.json').write_text(json.dumps(env,indent=2))
print('Created report.md, report.html, insertable LaTeX, and provenance.json')
print('Report HTML MB:',round((R/'report.html').stat().st_size/1e6,2))
