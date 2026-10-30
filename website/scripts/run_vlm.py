#!/usr/bin/env python3
"""
Run a VLM (or a simple baseline) over every scene and upload its trajectories to TrajLab.

    # try the pipeline end to end with a random stand-in "model"
    python scripts/run_vlm.py --server http://localhost:5000 --token changeme --model dummy --dry-run

    # straight-line baseline (shortest path in image space)
    python scripts/run_vlm.py --server ... --token ... --model shortest-path --baseline straight

    # your VLM, few-shot with 3 human-consensus examples from the train split, evaluated on test
    python scripts/run_vlm.py --server ... --token ... --model my-vlm-3shot --split test --shots 3

Uploaded coordinates are compared with human paths directly and drawn onto the training image
for the rating tasks. If your pipeline produces images instead, upload them on the admin page
(VLM images) or with scripts/upload_vlm_images.py.

Plug your model into `query_vlm()` below. It receives images (PIL) and a text prompt and must
return the model's raw text answer. Everything else (prompting, marker drawing, parsing,
validation, upload) is handled here. Only the standard library and Pillow are needed.
"""
from __future__ import annotations

import argparse
import io
import json
import random
import re
import sys
import urllib.error
import urllib.request

from PIL import Image, ImageDraw

PROMPT = """You are controlling a four-legged robot that walks among people on a university campus.
The image is the robot's own camera view. The blue dot at the bottom is the robot. {goal_text}

In this scene every route may break some unwritten social rule (passing close to people, walking on
grass, cutting through a group, a long detour, making others wait...). Choose the route a thoughtful
person would consider LEAST objectionable, or decide to wait if nothing is acceptable.

Coordinates are normalized image coordinates: x from 0 (left edge) to 1 (right edge), y from 0 (top)
to 1 (bottom). The robot starts at {start}.

Answer with JSON only, no other text:
{{"decision": "path" or "wait",
  "waypoints": [[x, y], ...],          // 2-8 points from the robot{goal_end}; omit if waiting
  "norms": [...],                      // rules your choice bends, from: {norm_keys}
  "confidence": 0.0-1.0,               // how likely most people would choose the same
  "rationale": "one sentence"}}
"""

EXAMPLE_INTRO = "Here are examples of the path most people chose in other scenes (drawn in pink):"


def query_vlm(images: list[Image.Image], prompt: str) -> str:
    """Send images + prompt to your vision-language model and return its text reply.

    `images` = in-context example images (with their human path drawn) followed by the target
    scene, in order. Encode each with e.g. `to_png_bytes(img)` and attach it the way your
    provider's SDK expects, then return the reply text.
    """
    raise NotImplementedError("Edit query_vlm() in scripts/run_vlm.py to call your model, "
                              "or run with --dry-run / --baseline straight.")


def dummy_vlm(images, prompt, scene):
    """Random stand-in so the pipeline can be tested without a model."""
    sx, sy = scene["start"]
    gx, gy = scene["goal"] or [sx, 0.45]
    side = random.choice([-1, 1])
    mid = [min(max(sx + side * random.uniform(0.15, 0.3), 0.02), 0.98), (sy + gy) / 2]
    return json.dumps({"decision": "path", "waypoints": [[sx, sy], mid, [gx, gy]],
                       "norms": ["terrain"], "confidence": round(random.uniform(0.4, 0.9), 2),
                       "rationale": "Random dry-run answer."})


# ---------------------------------------------------------------------------

def to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def http(url, token, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET")
    req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    return json.loads(data) if r.headers.get_content_type() == "application/json" else data


def fetch_image(url, token) -> Image.Image:
    return Image.open(io.BytesIO(http(url, token))).convert("RGB")


def draw_markers(img, start, goal, path=None):
    """Draw what participants see: blue robot dot, green goal flag, optional pink path."""
    img = img.copy()
    d = ImageDraw.Draw(img)
    W, H = img.size
    r = max(6, round((W * W + H * H) ** 0.5 / 120))
    if path:
        pts = [(x * W, y * H) for x, y in path]
        d.line(pts, fill="white", width=r // 2 + 4, joint="curve")
        d.line(pts, fill=(255, 45, 135), width=r // 2, joint="curve")
    x, y = start[0] * W, start[1] * H
    d.ellipse([x - r, y - r, x + r, y + r], fill=(36, 89, 255), outline="white", width=max(2, r // 4))
    if goal:
        x, y = goal[0] * W, goal[1] * H
        d.line([x, y, x, y - 2.5 * r], fill="white", width=max(3, r // 3))
        d.polygon([(x, y - 2.5 * r), (x + 1.6 * r, y - 2 * r), (x, y - 1.4 * r)], fill=(31, 157, 85), outline="white")
    return img


def parse_answer(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in model reply: {text[:200]!r}")
    raw = re.sub(r"//[^\n]*", "", m.group(0))  # tolerate copied comments
    ans = json.loads(raw)
    if ans.get("decision") not in ("path", "wait"):
        ans["decision"] = "path" if ans.get("waypoints") else "wait"
    if ans["decision"] == "path":
        pts = [[min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0)] for x, y in ans["waypoints"]]
        if len(pts) < 2:
            raise ValueError("path needs at least 2 points")
        ans["waypoints"] = pts
    return ans


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", required=True, help="e.g. http://localhost:5000")
    ap.add_argument("--token", required=True, help="API_TOKEN (defaults to the admin password on the server)")
    ap.add_argument("--model", required=True, help="name shown in the results table, e.g. gpt-vlm-3shot")
    ap.add_argument("--split", default="", help="only run on scenes in this split (e.g. test)")
    ap.add_argument("--shots", type=int, default=0, help="in-context examples from the train split")
    ap.add_argument("--baseline", choices=["straight"], help="skip the VLM and submit a simple baseline")
    ap.add_argument("--dry-run", action="store_true", help="use a random stand-in instead of a real VLM")
    ap.add_argument("--no-upload", action="store_true", help="print results instead of uploading")
    ap.add_argument("--style", choices=["curve", "line"], default="curve",
                    help="how the model's waypoints are joined: smooth curves or straight lines")
    args = ap.parse_args()
    base = args.server.rstrip("/")

    scenes = http(f"{base}/api/v1/scenes" + (f"?split={args.split}" if args.split else ""), args.token)
    norm_keys = [n["key"] for n in http(f"{base}/api/v1/norms", args.token)]
    examples = []
    if args.shots and not args.baseline:
        pool = [c for c in http(f"{base}/api/v1/consensus?split=train", args.token)
                if c["consensus_decision"] == "path" and not c["ambiguous"] and c["n_responses"] >= 3]
        examples = pool
        print(f"{len(pool)} unambiguous train scenes available as examples")

    out, failed = [], 0
    for sc in scenes:
        if args.baseline == "straight":
            end = sc["goal"] or [sc["start"][0], 0.45]
            out.append({"scene_id": sc["id"], "model": args.model, "source": "baseline", "decision": "path",
                        "kind": "polyline", "points": [sc["start"], end], "rationale": "Straight line to the goal."})
            continue
        goal_text = ("The green flag is where it needs to go." if sc["goal"]
                     else "It needs to continue forward through the scene.")
        prompt = PROMPT.format(goal_text=goal_text, start=sc["start"],
                               goal_end=f" to the flag at {sc['goal']}" if sc["goal"] else "",
                               norm_keys=", ".join(norm_keys))
        images = []
        shots = [e for e in examples if e["scene_id"] != sc["id"]][: args.shots]
        if shots:
            prompt = EXAMPLE_INTRO + "\n\n" + prompt
            for e in shots:
                img = fetch_image(e["image_url"], args.token)
                images.append(draw_markers(img, e["start"], e["goal"], e["consensus_curve"]))
        images.append(draw_markers(fetch_image(sc["image_url"], args.token), sc["start"], sc["goal"]))
        try:
            reply = dummy_vlm(images, prompt, sc) if args.dry_run else query_vlm(images, prompt)
            ans = parse_answer(reply)
        except NotImplementedError as e:
            sys.exit(str(e))
        except Exception as e:  # keep going; one bad reply shouldn't stop the run
            print(f"  ! {sc['title']}: {e}")
            failed += 1
            continue
        out.append({"scene_id": sc["id"], "model": args.model, "source": "vlm", "decision": ans["decision"],
                    "kind": "waypoints", "style": args.style, "points": ans.get("waypoints"), "norms": ans.get("norms") or [],
                    "confidence": ans.get("confidence"), "rationale": ans.get("rationale", ""),
                    "meta": {"shots": len(shots), "raw_reply": reply[:4000]}})
        print(f"  {sc['title']}: {ans['decision']} (confidence {ans.get('confidence')})")

    if args.no_upload:
        print(json.dumps(out, indent=1))
    elif out:
        r = http(f"{base}/api/v1/candidates", args.token, out)
        print(f"uploaded {r['imported']} paths for {args.model}" + (f"; errors: {r['errors']}" if r["errors"] else ""))
    if failed:
        print(f"{failed} scene(s) failed to parse")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        hint = " Check --token (it must match API_TOKEN, or ADMIN_PASSWORD if unset)." if e.code == 401 else ""
        sys.exit(f"Server answered {e.code} {e.reason} for {e.url}.{hint}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach the server: {e.reason}")
