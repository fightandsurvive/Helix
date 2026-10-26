"""
LoRA adapters for personality fine-tuning.

Rationale: 400 Morphy games cannot train 380M params without catastrophic
overfitting. LoRA adds a low-rank decomposition ΔW = BA · (α/r) to frozen
weights. With r=4, only ~3M trainable params — matching the Morphy corpus
size. With r=32, ~24M params — matching Carlsen's 4,000+ games.

Per-legend rank scaling:
    morphy:    4   ~3M params    400 games
    fischer:   8   ~6M params    950 games
    petrosian: 8   ~6M params   2000 games
    kasparov:  16  ~12M params  2500 games
    tal:       16  ~12M params  2800 games
    carlsen:   32  ~24M params  4000+ games

Adapters are ~20MB each (r=16) vs ~1.5GB per full checkpoint. Six adapters
plus one base ~1.6GB vs ~9GB for six full checkpoints.

Adapter files contain ONLY the A and B matrices — they are pure
state_dicts of tensors and load with weights_only=True. This is intentional:
adapters may be shared between installations, and weights_only=True is the
safest possible deserialization.
"""
from __future__ import annotations

import torch
import torch.nn as nn

from config.logging_setup import get_logger

log = get_logger(__name__)


class LoRALinear(nn.Module):
    """
    Wraps a frozen nn.Linear with a trainable low-rank ΔW = B @ A * scale.

    The base layer's parameters are set to requires_grad=False so the
    optimizer only sees the LoRA parameters. The forward path computes
    base(x) + scale * (x @ A.T @ B.T), which for small `r` adds negligible
    compute (~2% for r=16 at d=1024).

    B starts at zero (per the LoRA paper) so ΔW=0 at initialization and
    the adapter is a no-op until training begins. This is critical:
    attaching an adapter must not perturb the base model's behavior.
    """

    def __init__(self, base: nn.Linear, rank: int = 16, alpha: float = 32.0):
        super().__init__()
        self.base = base
        for p in self.base.parameters():
            p.requires_grad = False

        d_out, d_in = base.weight.shape
        self.A = nn.Parameter(torch.zeros(rank, d_in))
        self.B = nn.Parameter(torch.zeros(d_out, rank))
        nn.init.kaiming_uniform_(self.A, a=5 ** 0.5)
        # B stays zero — ΔW = BA = 0 at init
        self.scale = alpha / rank

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.base(x) + self.scale * (x @ self.A.T @ self.B.T)


def attach_lora(model: nn.Module, rank: int) -> nn.Module:
    """
    Replace target nn.Linear layers with LoRALinear wrappers, freezing
    the base model.

    Targets: attention input/output projections, policy/value head
    linears. Skips: piece/pos embeddings, RMSNorm, and any MoE gate
    (gates need continuous updates; wrapping them in low-rank would
    disable expert selection).
    """
    targets: list[str] = []
    for name, mod in model.named_modules():
        if not isinstance(mod, nn.Linear):
            continue
        if "gate" in name:
            continue  # MoE router — leave dense
        if any(k in name for k in (
            "attn.in_proj", "attn.out_proj",
            "policy_head", "value_head",
        )):
            targets.append(name)

    for name in targets:
        parent_path, leaf = name.rsplit(".", 1)
        parent = model.get_submodule(parent_path)
        setattr(parent, leaf, LoRALinear(getattr(parent, leaf), rank=rank))

    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    log.info("lora_attached", rank=rank, trainable_params=trainable)
    return model


def save_adapter(model: nn.Module, path: str) -> None:
    """
    Save ONLY the LoRA parameters — portable across base versions.

    The saved file is a pure dict of tensors, loadable with
    weights_only=True. No metadata, no nested dicts — just A and B
    matrices keyed by module path.
    """
    state = {
        k: v for k, v in model.state_dict().items()
        if k.endswith(".A") or k.endswith(".B")
    }
    torch.save(state, path)
    log.info("adapter_saved", path=path, params=len(state))


def load_adapter(model: nn.Module, path: str) -> nn.Module:
    """
    Load LoRA params into a model that already has LoRALinear wrappers
    applied via attach_lora().

    Uses strict=False because the adapter file only contains A/B tensors,
    not the full state dict. Base weights must be loaded separately
    (typically by re-instantiating the base model and copying its
    state_dict before attaching LoRA).
    """
    state = torch.load(path, map_location="cuda", weights_only=True)
    missing, unexpected = model.load_state_dict(state, strict=False)
    if unexpected:
        log.warning("adapter_unexpected_keys", keys=unexpected[:5])
    return model
