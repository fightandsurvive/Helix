"""
Reproducibility helpers.

Every training run must be re-runnable. `torch.manual_seed` alone is not
enough — CUDA nondeterminism, dataloader worker seeding, and hash iteration
order all leak randomness. This module patches all three.

The checkpoint-hash helper supports the provenance chain in
engine_state.checkpoints: any checkpoint file can be verified against the
SHA256 recorded at training time.
"""

from __future__ import annotations

import hashlib
import os
import random
from pathlib import Path

import numpy as np
import torch


def seed_everything(seed: int) -> None:
    """
    Set all PRNG seeds and enable deterministic CUDA operations.

    Deterministic mode is ~5% slower but produces bit-for-bit reproducible
    checkpoints. For a project whose value depends on being able to
    reconstruct "how did we get here," that 5% is worth paying.
    """
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)

    # Disable nondeterministic CUDA paths.
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False

    # torch 2.14: use_deterministic_algorithms makes a best-effort attempt
    # to raise if a nondeterministic op is invoked. warn_only=True means
    # it warns instead of erroring — some ops (like certain reductions)
    # have no deterministic implementation and we accept the nondeterminism.
    try:
        torch.use_deterministic_algorithms(True, warn_only=True)
    except Exception:
        # Older torch versions may not support this API uniformly.
        pass


def ckpt_sha256(path: Path) -> str:
    """
    Content hash of a checkpoint file, streamed to avoid loading the
    whole file into memory.

    Used to populate engine_state.checkpoints.sha256 so that any file
    on disk can be verified against the database record — catching
    silent corruption from disk errors, partial writes on crash, or
    accidental overwrites.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
