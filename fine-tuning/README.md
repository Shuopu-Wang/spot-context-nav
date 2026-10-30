# Fine-tuning

Training and evaluation for VLMs / path models used in this project.

## Intended contents

- Training scripts and configs (LoRA / full FT, few-shot baselines, etc.)
- Prompt templates and shot-selection helpers
- Eval against TrajLab human paths (exports from `website/`)
- Checkpoints **should not** live in git — keep them on shared disk and point configs at those paths

## Related

- Human labels and compiled trajectories: export from the TrajLab admin **Data** tab, or `website/scripts/analyze_export.py`
- Prior offline analysis: [`../experiments/trajlab_ai_pilot/`](../experiments/trajlab_ai_pilot/)
