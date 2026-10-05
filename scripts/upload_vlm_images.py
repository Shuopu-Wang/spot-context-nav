#!/usr/bin/env python3
"""
Upload a folder of images drawn by a VLM, matching each to its training image by file name.

    python scripts/upload_vlm_images.py --server http://localhost:5000 --token $API_TOKEN \\
        --model gpt-4o-fewshot path/to/outputs/

A file matches a training image when its name contains the training image's original file name
(without extension) or its title, e.g. "welch_crowd_gpt4o.png" -> "welch_crowd.jpg", or when it
contains the first 8 characters of the training image's id. Use --dry-run to check the matches
first. Unmatched files are uploaded unlinked (they can still be rated) unless --skip-unmatched.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", Path(str(s or "")).stem.lower()).strip()


def match(name, scenes):
    f = norm(name)
    compact = f.replace(" ", "")
    best, best_len = None, 0
    for s in scenes:
        if s["id"][:8] in compact:
            return s
        for key in (norm(s.get("source_name")), norm(s.get("title"))):
            if len(key) >= 3 and key in f and len(key) > best_len:
                best, best_len = s, len(key)
    return best


def request(url, token, data=None, content_type=None):
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    req.add_header("Authorization", f"Bearer {token}")
    if content_type:
        req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--server", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--model", required=True, help="name shown in the results, e.g. gpt-4o-fewshot")
    ap.add_argument("--source", default="vlm", choices=["vlm", "baseline", "other"])
    ap.add_argument("--dry-run", action="store_true", help="only print the matches")
    ap.add_argument("--skip-unmatched", action="store_true")
    ap.add_argument("--batch", type=int, default=50, help="files per request")
    args = ap.parse_args()
    base = args.server.rstrip("/")

    files = sorted(p for p in Path(args.folder).iterdir() if p.suffix.lower() in IMAGE_EXT)
    if not files:
        sys.exit("No images found in that folder.")
    scenes = request(f"{base}/api/v1/scenes", args.token)
    plan = []
    for f in files:
        s = match(f.name, scenes)
        print(f"  {f.name:<50} -> {s['title'] if s else '(not linked)'}")
        if s or not args.skip_unmatched:
            plan.append((f, s))
    print(f"{sum(1 for _, s in plan if s)} of {len(files)} matched.")
    if args.dry_run:
        return
    total = 0
    for i in range(0, len(plan), args.batch):
        chunk = plan[i:i + args.batch]
        boundary = uuid.uuid4().hex
        body = b""
        fields = {"model": args.model, "source": args.source}
        for j, (_, s) in enumerate(chunk):
            if s:
                fields[f"scene_{j}"] = s["id"]
        for k, v in fields.items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for f, _ in chunk:
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{f.name}"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n").encode() + f.read_bytes() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        r = request(f"{base}/api/v1/vlm_images", args.token, body, f"multipart/form-data; boundary={boundary}")
        total += len(r["created"])
    print(f"Uploaded {total} image(s) for {args.model}.")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        hint = " Check --token (API_TOKEN, or ADMIN_PASSWORD if unset)." if e.code == 401 else ""
        sys.exit(f"Server answered {e.code}: {e.read()[:300].decode(errors='replace')}{hint}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach the server: {e.reason}")
