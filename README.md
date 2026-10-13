# TrajLab

A self-hosted website for a social-navigation study:

1. **Upload training images**: egocentric photos (or short clips) of over-constrained scenes.
2. **Admins draw candidate paths** on each image: as many as they like, with straight lines or
   Bezier curves, plus an optional "the robot waits" option.
3. **People sign in** with their name, email and optional ID, as a *research team member* (needs the
   team access code, gets **every** image) or a *survey participant* (gets **5 random** images,
   favouring the images with the fewest rankings).
4. **They rank the paths** on each image from best to worst (shown in a shuffled order with neutral
   letters) and mark any path that is **not socially acceptable**.
5. Each image's folder gets the **scores** (normalized Borda count), the **best-ranked acceptable path**,
   rater agreement (**Kendall's W**) and every ranking. The best path per image is the training target.
6. Optional: upload images your VLM drew and have each person **rate 10** of them at the end
   (set the count to 0 in Settings to skip).
7. **Download the dataset** as per-image folders plus CSV tables, or a copy without names and emails.

The earlier mode, where participants draw their own paths, is still available under
*Settings > What participants do with each photo*.

No build step, no external services. Python 3.10+.

---

## Run it

### On your laptop (and anyone on the same Wi-Fi)

```bash
cd trajlab
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
ADMIN_PASSWORD=pick-something python app.py              # PowerShell: $env:ADMIN_PASSWORD="pick-something"; python app.py
```

The terminal prints the study link, a **Same Wi-Fi** link for phones, the admin page (`/admin`), the
**team access code**, and the **dataset folder**. Everything lives in `data/`: the database, uploaded
media, and `data/dataset/` with one folder per training image. Back that folder up.

### Share it on the internet

| Option | Effort | Where the data lives | Notes |
|---|---|---|---|
| **Tunnel from a laptop** | 2 minutes | your laptop's `data/` | `cloudflared tunnel --url http://localhost:5000` (or `ngrok http 5000`) prints a public https link. The laptop has to stay on. |
| **Render** | ~10 minutes | Render persistent disk | Push this folder to GitHub, then *New > Blueprint*. `render.yaml` sets up the service with a disk at `/data`. Disks need a paid instance. |
| **Any VPS / lab server** | ~15 minutes | wherever you mount `/data` | `docker build -t trajlab . && docker run -d -p 80:8000 -v $PWD/data:/data -e ADMIN_PASSWORD=... trajlab` |
| **Railway / Fly.io** | ~10 minutes | a volume at `/data` | Use the Dockerfile; attach a volume and set `DATA_DIR=/data`. |

> **Free hosting tiers usually erase their disk on restart.** Attach a persistent disk or volume and
> download the dataset regularly.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ADMIN_PASSWORD` | `changeme` | Admin sign-in. **Change it before sharing a public link.** |
| `API_TOKEN` | same as `ADMIN_PASSWORD` | Bearer token for scripts (`/api/v1/...`). |
| `DATA_DIR` | `./data` | Database, media and the dataset folder. |
| `PORT` | `5000` (`8000` in Docker) | HTTP port. |
| `SECRET_KEY` | generated, saved in `DATA_DIR` | Signs admin sessions. |
| `MAX_UPLOAD_MB` | `1024` | Largest upload. |

Everything else (texts, counts, team code, rating questions, analysis settings) is under *Admin > Settings*.

Upgrading from the first version: just run the new code on the same `DATA_DIR`; the database is
upgraded in place.

---

## Running a study

1. **Training images** (admin): drop photos, a `.zip` of photos, or clips. For a clip, the last frame
   is the moment people draw on (change it on the image's page). Open each image and set the robot's
   **start** and a **goal flag** (*Start & goal*). A shared goal keeps paths comparable.
2. **Share links** (admin overview): the survey link for everyone, and the team link, which fills in the
   team access code. Someone choosing *Research team member* without the right code is turned away.
3. People sign in. Entering the same email again resumes where they left off; team members who come
   back later automatically get any images added since, and anyone who finished drawing before VLM
   images were uploaded gets a rating set when they come back. (Resuming works by email alone, so it
   is a convenience, not a security boundary.)
4. **VLM images** (admin): enter the model name, drop the VLM's output images, check the automatic
   matches (e.g. `welch_crowd_gpt4o.png` → `welch_crowd.jpg`), upload. Upload each model separately.
5. Everyone rates **10** VLM images after drawing (configurable), balanced so every image gets a
   similar number of ratings and nobody sees the same photo twice where possible.
6. Read **Results**, then **Data > Download**.

### Ranking

Open a training image in the admin, choose **Draw paths**, click points, **Save path**, repeat. Only
photos with at least one path are handed out. Each participant sees that photo's paths in their own
shuffled order, labelled A, B, C…, taps them from best to worst (the last one fills itself in; tapping
a ranked path undoes from there), and can tick **Not socially acceptable** on any of them.

Per path the site reports: **score** (normalized Borda count, 100% = always ranked first, 0% =
always last, averaged over rankings), average position, share ranked best, and share marked not
socially acceptable. The **best path** is the highest-scoring path that fewer than half the raters
marked unacceptable; if every path fails that, the image is flagged *none acceptable*. **Kendall's W**
(0 = no agreement, 1 = everyone gave the same order) shows how much raters agreed. If you add or delete
paths after people have ranked, their earlier rankings still count for the paths they saw; Kendall's W
uses only rankings that covered the current set.

Files per image folder: `ranked_paths.jpg` (all paths, numbered by overall rank), `best_path.jpg`,
`ranking.json` (every path with coordinates and scores, plus each ranking) and `rankings.csv`.
The download adds `csv/path_scores.csv` and `csv/rankings.csv`.

## The drawing widget

- Tap the photo to place points; they connect from the robot (blue dot) to the flag.
- **Straight lines / Curves** switches every segment. Curves are cubic Bezier segments with smooth
  automatic handles.
- Drag the small **diamond** in the middle of a segment to bend it. Tap a diamond to select that
  segment: make just that one straight or curved, or drag its two square **Bezier handles**.
- Drag a point to move it, double-tap to remove it; Undo, Clear, *Go straight*, *I'd wait*.
- Then: which unwritten rules the choice bends, how sure they are, optional comment, **Submit**.

The exact geometry (points, segment types, Bezier control points) is stored, and the analysis uses
the same curve the participant saw.

---

## The dataset folder

`data/dataset/` on the server, and `images/` in the download, kept up to date as people submit:

```
<image-name>__<id>/
    original.jpg                  the training image
    annotations/S0007.jpg         each participant's path drawn on the original (named by participant code)
    annotations/excluded/...      annotations you excluded in the admin
    vlm/gpt4o__<file>.png         VLM images of this training image
    all_paths.jpg                 every human path coloured by route; compiled path in black with its spread
    compiled_path.jpg             the compiled trajectory alone (ready-made in-context example)
    paths.json                    every path as coordinates, routes, outliers, the compiled path, VLM metrics
    similarity.csv                pairwise similarity between participants' paths
    frechet_distance.csv          the underlying distances
_vlm_unlinked/                    VLM images not matched to a training image
```

The download adds `csv/participants.csv`, `annotations.csv`, `images.csv`, `vlm_images.csv`,
`vlm_ratings.csv` (one row per rating, one column per question), `model_summary.csv`,
`reliability.csv`, and `dataset.json` with everything. **Without names and emails** strips personal
data; files and tables only ever use participant codes (`T001` team, `S0001` survey).

Coordinates are normalized image coordinates: `x = pixel_x / width`, `y = pixel_y / height`, origin
top-left.

**Collected on several laptops?** Download each copy (*Download everything*) and upload it on one of
them under *Data > Merge*. Records match by ID, so nothing duplicates; clashing participant codes are
renumbered.

---

## How paths are compared and combined

**Preprocessing.** Each path is sampled from its exact geometry (lines and Bezier curves), then
resampled to 64 points evenly spaced along its length, so the number of points someone placed and how
they spaced them don't matter. Distances are in pixels, reported as a **percentage of the image
diagonal**.

**Distance between two paths**

| Metric | What it measures | Why |
|---|---|---|
| **Discrete Fréchet distance** (primary) | Largest gap between two paths under the best forward-moving alignment. | Respects direction and catches a brief excursion (cutting through a group for two steps) that averages hide. |
| **RMSE / MSE** | Root-mean-square gap between length-matched points (MSE = RMSE²). | The mean-squared-error view. Smooth, but blind to short excursions. |
| **DTW** | Mean gap along the optimal dynamic-time-warping alignment. | From the experimental plan; tolerant of local spacing differences. |

**Similarity score** = 1 / (1 + Fréchet ÷ τ), where τ is the same-route threshold (10% of the
diagonal by default): 100% for identical paths, 50% exactly at the threshold. Each image page shows the
full similarity matrix, grouped by route.

**Routes.** Passing left or right of a group can be equally acceptable but geometrically far apart,
and the average of the two walks straight through the group. So paths are first grouped into
**routes** (average-linkage clustering on Fréchet distance, cut at τ); *wait* counts as one more choice.

**Outliers.** A path is an outlier when **nobody else drew anything similar**: its distance to the
nearest other path exceeds both τ and the robust cut median + 3·1.4826·MAD of those nearest-neighbour
distances (only with 5+ paths). A route that at least two people drew independently is never flagged,
so legitimate minority opinions are kept; a lone scribble is caught. By default outliers are left out of
the vote and the compiled path (still in the dataset, marked).

**Compiled trajectory.** Take the most popular route. Within it, trim members whose Fréchet distance to
the route's most central path (medoid) is a robust outlier (median + 3·MAD, with 4+ members). The
compiled path is the **point-wise average** of the remaining members, with a **spread band** (±1 SD
of their sideways offsets). If most people waited, the compiled result is *wait*. You can switch to the
medoid (a real drawn path) in Settings, or replace it with a team-agreed path on the image page
(*Add a path* → *Use as this image's compiled path*), matching the discussion step in your labeling
protocol.

**Disagreement** is recorded, not discarded: share choosing the top route, number of routes, choice
entropy, mean similarity, rule-tag overlap. An image is **ambiguous** when the top choice gets under
60% (adjustable). All analysis views can be filtered to team-only or survey-only annotations.

**Caveat.** Distances are in image space; in an egocentric view a pixel near the horizon covers more
ground than one near the robot. With camera height and intrinsics you could project points to the
ground plane first; `analysis.path_curve` is the one place to add it.

---

## Rating VLM images

Default questions (edit them under *Settings*; one line each, `key | yesno | text` or
`key | 1-7 | text | low label | high label`):

| Key | Question | Construct |
|---|---|---|
| acceptable | Is this path acceptable for a robot in this situation? (Yes / No) | overall acceptability |
| comfortable | If you were one of the people in this scene, how comfortable would you feel…? (1–7) | comfort (Kruse et al., 2013) |
| safe | How safe would you feel around the robot as it takes this path? (1–7) | perceived safety (Godspeed V, Bartneck et al., 2009) |
| reasonable | How reasonable is this path, given the situation? (1–7) | reasonableness |
| appropriate | How socially appropriate (polite) is this path toward the people around it? (1–7) | sociability (Kruse et al., 2013) |
| natural | How natural is this path? Is it how a considerate person would walk? (1–7) | naturalness (Kruse et al., 2013) |
| predictable | Could people nearby easily tell where the robot is going? (1–7) | legibility (Dragan et al., 2013) |

Plus *which unwritten rules does this path break* and an optional comment. Raters can hold a button to
see the photo without the path, and enlarge the image.

**Results** shows each model's mean per question with a 95% bootstrap interval (each VLM image counts
once), and **Krippendorff's alpha** per question for inter-rater reliability. Each rating records the
image actually shown, its position in the set, the time taken, and whether the rater drew the same
photo themselves (so you can check for that bias).

**Comparing VLM paths with human paths.** A VLM image is only pixels, so to compare its path with
people's, either upload coordinates (JSON, or `scripts/run_vlm.py`) or open the VLM image and use
**Trace the path** (click along the line it drew). Traced or uploaded paths get *same route as people*,
Fréchet / similarity / MSE / DTW to the compiled path, and a human percentile.

**Hidden controls (optional).** *Settings > Of those, hidden human-consensus controls* mixes the
compiled human path into each rating set as a reference. It is drawn in the site's style, so unless you
also choose *Rating shows: re-drawn in one style* (which re-draws every VLM path that has coordinates
the same way), raters may be able to tell controls apart.

---

## Scripts and API

```bash
# upload a folder of VLM images, matched by file name (try --dry-run first)
python scripts/upload_vlm_images.py --server https://your-site --token $API_TOKEN --model gpt-4o-fewshot outputs/

# run a VLM over every image and upload its paths as coordinates (fill in query_vlm() first)
python scripts/run_vlm.py --server ... --token ... --model my-vlm-3shot --shots 3 --split test
python scripts/run_vlm.py --server ... --token ... --model shortest-path --baseline straight

# re-run the analysis offline on a download, e.g. survey annotations only, different threshold
python scripts/analyze_export.py trajlab_dataset.zip --role survey --threshold 0.08 --out results/
```

| Method | Path | |
|---|---|---|
| GET | `/api/v1/scenes?split=test` | Active training images: image URL, size, start, goal |
| GET | `/api/v1/consensus?split=train` | Compiled human trajectory per image, agreement, ambiguity |
| GET | `/api/v1/norms` | Rule tags |
| POST | `/api/v1/vlm_images` | multipart: `files`, `model`, `source`, `scene_<i>` = training image id for the i-th file |
| POST | `/api/v1/candidates` | JSON list: `scene_id` or `scene_title`, `model`, `decision` (`path`/`wait`), `points`, optional `segments`, `style` (`curve`/`line`), `confidence`, `norms`, `rationale` |

Send `Authorization: Bearer $API_TOKEN`.

---

## Project layout

```
app.py              Flask app: participant flow, admin API, pipeline API
analysis.py         Path geometry (lines + Bezier), Fréchet / RMSE / DTW, similarity, routes, outliers,
                    compiled trajectory, Krippendorff's alpha, rating summaries (numpy only)
dataset.py          Annotated-image rendering, the per-image dataset folders, export and merge
templates/, static/ Participant and admin pages; static/js/geometry.js mirrors analysis.py's geometry
scripts/            upload_vlm_images.py, run_vlm.py, analyze_export.py
tests/              python tests/test_analysis.py
```

If you'll publish data from outside participants, check whether your university's IRB needs to review
the study. The consent text is editable under Settings.
