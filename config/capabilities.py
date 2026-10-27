"""
Runtime capability manifest.

Probes the machine (GPU, torch, checkpoints, Ollama, Stockfish, Syzygy)
and combines with .env overrides. The app reads this once at startup
and uses it to decide what to show and refuse.

Design: capabilities are PROBED, not assumed. If the user sets
HELIX_ENGINE_ENABLED=true but there's no checkpoint, the probe reports
'engine_available: false, reason: no_checkpoint'. UI shows actionable
guidance rather than a broken button.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from config.logging_setup import get_logger
from config.settings import settings

log = get_logger(__name__)


@dataclass(frozen=True)
class Capabilities:
    engine_requested: bool
    training_requested: bool
    coach_llm_requested: bool

    engine_available: bool
    training_available: bool
    coach_llm_available: bool
    stockfish_available: bool
    syzygy_available: bool
    stockfish_path: str
    gpu_name: str | None
    gpu_vram_gb: float | None
    is_blackwell: bool

    engine_reason: str
    training_reason: str
    coach_reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def probe() -> Capabilities:
    engine_requested = getattr(settings, "engine_enabled", False)
    training_requested = getattr(settings, "training_enabled", False)
    coach_requested = getattr(settings, "coach_llm_enabled", True)

    # GPU probe — heavy import, only paid once at startup
    gpu_name = None
    gpu_vram = None
    is_blackwell = False
    cuda_ok = False
    try:
        import torch

        if torch.cuda.is_available():
            cuda_ok = True
            props = torch.cuda.get_device_properties(0)
            gpu_name = props.name
            gpu_vram = round(props.total_memory / 1e9, 1)
            major, _ = torch.cuda.get_device_capability()
            is_blackwell = major >= 10
    except Exception as e:
        log.info("torch_probe_skipped", reason=str(e))

    ckpt_dir = Path(settings.checkpoints_dir)
    has_ckpt = (ckpt_dir / "distilled.pt").exists()

    engine_available = engine_requested and cuda_ok and has_ckpt
    if not engine_requested:
        engine_reason = "Disabled via HELIX_ENGINE_ENABLED=false"
    elif not has_ckpt:
        engine_reason = f"No checkpoint at {ckpt_dir / 'distilled.pt'} — run the training pipeline"
    elif not cuda_ok:
        engine_reason = "No CUDA device available"
    else:
        engine_reason = ""

    training_available = training_requested and cuda_ok
    if not training_requested:
        training_reason = "Disabled via HELIX_TRAINING_ENABLED=false"
    elif not cuda_ok:
        training_reason = "No CUDA device available"
    elif not is_blackwell:
        training_reason = f"GPU is {gpu_name} — training will run BF16 (no NVFP4)"
    else:
        training_reason = ""

    # Ollama probe
    import requests

    try:
        r = requests.get(f"{settings.ollama_url}/api/tags", timeout=2)
        ollama_ok = r.status_code == 200
    except Exception:
        ollama_ok = False

    coach_available = coach_requested and ollama_ok
    if not coach_requested:
        coach_reason = "Disabled via HELIX_COACH_LLM_ENABLED=false"
    elif not ollama_ok:
        coach_reason = "Ollama not reachable — check that the service is running"
    else:
        coach_reason = ""

    return Capabilities(
        engine_requested=engine_requested,
        training_requested=training_requested,
        coach_llm_requested=coach_requested,
        engine_available=engine_available,
        training_available=training_available,
        coach_llm_available=coach_available,
        stockfish_available=Path(settings.stockfish_path).exists(),
        stockfish_path=str(settings.stockfish_path),
        syzygy_available=Path(settings.syzygy_path).exists(),
        gpu_name=gpu_name,
        gpu_vram_gb=gpu_vram,
        is_blackwell=is_blackwell,
        engine_reason=engine_reason,
        training_reason=training_reason,
        coach_reason=coach_reason,
    )


if __name__ == "__main__":
    import json

    print(json.dumps(probe().to_dict(), indent=2))
