# spot-context-nav

Context-aware social navigation for Spot: human path labels, VLM evaluation, and model fine-tuning.

| Area | Path | What lives here |
|------|------|-----------------|
| **Website (TrajLab)** | [`website/`](website/) | Study site: draw paths, rate VLM outputs, compile consensus trajectories |
| **Experiments** | [`experiments/`](experiments/) | Offline pilots and analysis (e.g. Astra TrajLab AI pilot) |
| **Fine-tuning** | [`fine-tuning/`](fine-tuning/) | Training / eval for path and VLM models |

## Quick start (website)

```bash
cd website
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ADMIN_PASSWORD=pick-something python app.py
```

Full TrajLab docs: [`website/README.md`](website/README.md).

## Deploy (TrajLab on Render)

From the **repo root**, use [`render.yaml`](render.yaml) (Docker context is `website/`). Or build from `website/` with its own Dockerfile:

```bash
cd website
docker build -t trajlab .
docker run -d -p 80:8000 -v "$PWD/data:/data" -e ADMIN_PASSWORD=... trajlab
```
