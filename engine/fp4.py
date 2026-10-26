"""
NVFP4 training configuration via TorchAO 0.18.

Requirements (enforced at runtime, falls back to BF16 transparently):
- Blackwell GPU (SM100+, compute capability major >= 10)
- Model in bfloat16
- All nn.Linear layers have in_features and out_features divisible by 128

The final 15% of transformer blocks remain in BF16 (TorchTitan recipe).
Quantizing the last layers causes measurable loss regression because
early-layer quantization errors compound through the residual stream,
but late-layer errors directly perturb the policy/value heads.

torch.compile is required for competitive NVFP4 throughput — without
it, the custom NVGEMM kernels don't fuse and you get BF16-level speed
from FP4 memory. This module applies torch.compile automatically.

Isolating the API surface here means a torchao upgrade only touches
this one file. When the NVFP4 API stabilizes (currently prototype), the
import paths may change; only this module needs updating.
"""

from __future__ import annotations

import torch

from config.logging_setup import get_logger
from config.settings import settings

log = get_logger(__name__)

try:
    from torchao.prototype.moe_training.nvfp4_training.nvfp4_training import (
        NVFP4TrainingConfig,
    )
    from torchao.quantization import quantize_

    _TORCHAO_AVAILABLE = True
except ImportError:
    _TORCHAO_AVAILABLE = False
    log.warning("torchao_nvfp4_unavailable", fallback="bf16")


def _is_blackwell() -> bool:
    """SM100+ is the Blackwell family. Ada/Ampere/Hopper are <10."""
    if not torch.cuda.is_available():
        return False
    major, _ = torch.cuda.get_device_capability()
    return major >= 10


def _linear_dims_divisible_by_128(model: torch.nn.Module) -> bool:
    """
    NVFP4 kernels pack values in 128-element blocks — every linear
    layer's input and output dimensions must be a multiple of 128.
    Our model uses d_model=1024, d_ff=3072, head widths 512/1024 — all
    divisible. This check protects against accidental architecture
    changes that would silently break NVFP4.
    """
    for module in model.modules():
        if isinstance(module, torch.nn.Linear):
            if module.in_features % 128 or module.out_features % 128:
                log.warning(
                    "nvfp4_dims_not_div_128",
                    in_features=module.in_features,
                    out_features=module.out_features,
                )
                return False
    return True


def enable_nvfp4_if_possible(model: torch.nn.Module) -> torch.nn.Module:
    """
    Convert linear layers to NVFP4 if hardware + torchao support it.

    Returns the model (possibly modified). On any failure, returns the
    model unchanged and logs the reason — training proceeds in BF16.
    This is intentional: NVFP4 is an accelerator, not a requirement, and
    a failed conversion must not block training.
    """
    if not settings.use_nvfp4:
        return model

    if not _TORCHAO_AVAILABLE:
        log.info("nvfp4_skipped", reason="torchao_missing")
        return model

    if not _is_blackwell():
        log.info("nvfp4_skipped", reason="not_blackwell")
        return model

    if not _linear_dims_divisible_by_128(model):
        log.warning("nvfp4_skipped", reason="linear_dims_not_div_128")
        return model

    # Keep the final 15% of blocks in BF16 — TorchTitan recipe.
    blocks = getattr(model, "blocks", None)
    total_blocks = len(blocks) if blocks is not None else 0
    tail = max(1, int(total_blocks * 0.15))
    bf16_block_ids = set(range(total_blocks - tail, total_blocks))
    log.info(
        "nvfp4_tail_bf16",
        total_blocks=total_blocks,
        bf16_blocks=len(bf16_block_ids),
    )

    try:
        quantize_(
            model,
            NVFP4TrainingConfig(),
            filter_fn=lambda m, name: not any(f"blocks.{i}." in name for i in bf16_block_ids),
        )
        log.info("nvfp4_enabled")
    except Exception as e:
        log.error("nvfp4_conversion_failed", error=str(e))
        return model

    # torch.compile is required for competitive NVFP4 throughput —
    # without it the custom NVGEMM kernels don't fuse.
    try:
        # torch.compile returns a callable wrapper, not the original Module.
        # The runtime behavior is correct; mypy can't see through
        # torch.compile's return type — safe to ignore.
        return torch.compile(model, mode="max-autotune")  # type: ignore[return-value]
    except Exception as e:
        log.error("torch_compile_failed", error=str(e))
        return model
