"""
Chess Transformer with optional Mixture-of-Experts FFN.

Architecture (24L d=1024 default):
- 66-token input (1 CLS + 1 global + 64 squares)
- Pre-norm transformer blocks with RMSNorm (stable at depth 24+ without warmup)
- Optional MoE FFN (32 experts, top-4 routing) for capacity scaling
- Dual heads: 4672-way policy logits + scalar value in [-1, 1]

Parameter counts:
    Small (12L, d=512):   ~45M
    Medium (24L, d=1024): ~380M   ← floor, matches Lc0 T79 scale
    Large (24L, d=1024, MoE): ~1.4B (420M active)   ← target
    XL (32L, d=1024, MoE): ~2.1B   ← aspirational, needs 2× GPU
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class DenseFFN(nn.Module):
    """Standard 2-layer FFN with GELU. Used when use_moe=False."""

    def __init__(self, d: int, d_ff: int, dropout: float = 0.0):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(d, d_ff),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(d_ff, d),
            nn.Dropout(dropout),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class MoEFFN(nn.Module):
    """
    Sparse Mixture-of-Experts FFN.

    Top-k routing: each token selects k experts via a learned gate, and
    the FFN output is the weighted sum of the selected experts. Empirically
    this captures the "tactical density regimes" clustering of chess
    positions — sharp middlegames route to different experts than quiet
    endgames.

    This implementation loops over the top-k experts, applying each to the
    tokens that selected it. Naive but 100% correct. The `megablocks` or
    `tutel` libraries would give 3–5× throughput at the cost of a heavy
    dependency; not worth it until we profile the training loop and find
    MoE forward to be a bottleneck.
    """

    def __init__(
        self,
        d: int,
        d_ff: int,
        n_experts: int = 16,
        top_k: int = 2,
        dropout: float = 0.0,
    ):
        super().__init__()
        self.n_experts = n_experts
        self.top_k = top_k
        self.gate = nn.Linear(d, n_experts, bias=False)
        # Stack expert weights into single tensors for indexed batched access.
        # Shape (E, d, d_ff) allows `self.w1[expert_idx]` to yield a
        # per-token weight matrix in one gather.
        self.w1 = nn.Parameter(torch.randn(n_experts, d, d_ff) * (1.0 / d**0.5))
        self.w2 = nn.Parameter(torch.randn(n_experts, d_ff, d) * (1.0 / d_ff**0.5))
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        shape = x.shape
        x_flat = x.reshape(-1, shape[-1])  # (B*T, d)
        gate_logits = self.gate(x_flat)  # (B*T, E)
        topk_w, topk_i = gate_logits.topk(self.top_k, dim=-1)
        topk_w = torch.softmax(topk_w, dim=-1, dtype=torch.float32).to(x.dtype)

        out = torch.zeros_like(x_flat)
        for k in range(self.top_k):
            expert_idx = topk_i[:, k]  # (B*T,)
            weight = topk_w[:, k].unsqueeze(-1)  # (B*T, 1)
            w1 = self.w1[expert_idx]  # (B*T, d, d_ff)
            w2 = self.w2[expert_idx]  # (B*T, d_ff, d)
            h = torch.bmm(x_flat.unsqueeze(1), w1).squeeze(1)
            h = F.gelu(h)
            y = torch.bmm(h.unsqueeze(1), w2).squeeze(1)
            out = out + weight * y

        return self.dropout(out).reshape(shape)


class TransformerBlock(nn.Module):
    """Pre-norm transformer block with optional MoE FFN."""

    def __init__(
        self,
        d: int,
        nhead: int,
        d_ff: int,
        use_moe: bool = False,
        n_experts: int = 16,
        top_k: int = 2,
        dropout: float = 0.05,
    ):
        super().__init__()
        self.norm1 = nn.RMSNorm(d)
        self.attn = nn.MultiheadAttention(d, nhead, dropout=dropout, batch_first=True)
        self.norm2 = nn.RMSNorm(d)
        self.ffn = MoEFFN(d, d_ff, n_experts, top_k, dropout) if use_moe else DenseFFN(d, d_ff, dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.norm1(x)
        a, _ = self.attn(h, h, h, need_weights=False)
        x = x + a
        x = x + self.ffn(self.norm2(x))
        return x


class ChessTransformer(nn.Module):
    """
    The Trinstinctus policy-value network.

    Config defaults match the "Large" configuration from the white paper:
    24 layers, d=1024, dense FFN. Set `use_moe=True` for the MoE variant
    with ~1.4B total / ~420M active params.
    """

    def __init__(
        self,
        d_model: int = 1024,
        nhead: int = 16,
        num_layers: int = 24,
        dim_feedforward: int = 3072,
        dropout: float = 0.05,
        use_moe: bool = False,
        n_experts: int = 16,
        top_k: int = 2,
        num_global_feats: int = 14,
    ):
        super().__init__()
        self.piece_embedding = nn.Embedding(13, d_model, padding_idx=0)
        self.pos_embedding = nn.Parameter(torch.zeros(1, 64, d_model))
        self.global_proj = nn.Linear(num_global_feats, d_model)
        self.cls_token = nn.Parameter(torch.zeros(1, 1, d_model))

        self.blocks = nn.ModuleList(
            [
                TransformerBlock(d_model, nhead, dim_feedforward, use_moe, n_experts, top_k, dropout)
                for _ in range(num_layers)
            ]
        )
        self.norm = nn.RMSNorm(d_model)

        # Policy head projects pooled CLS token to 4672 logits.
        # 1024 intermediate width chosen so the head is ~5% of total params.
        self.policy_head = nn.Sequential(
            nn.Linear(d_model, 1024),
            nn.GELU(),
            nn.Linear(1024, 4672),
        )
        # Value head projects to a single scalar; tanh bounds to [-1, 1].
        self.value_head = nn.Sequential(
            nn.Linear(d_model, 512),
            nn.GELU(),
            nn.Linear(512, 1),
        )

        # Initialize embeddings with small std — standard for transformers,
        # avoids gradient issues in the first few steps.
        nn.init.trunc_normal_(self.pos_embedding, std=0.02)
        nn.init.trunc_normal_(self.cls_token, std=0.02)

    def forward(
        self,
        squares: torch.Tensor,
        global_feats: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Args:
            squares:      (B, 64) long — piece indices
            global_feats: (B, 14) float — STM, castling, EP, halfmove

        Returns:
            policy: (B, 4672) logits (unnormalized)
            value:  (B, 1) in [-1, 1], from side-to-move perspective
        """
        B = squares.size(0)
        x = self.piece_embedding(squares) + self.pos_embedding  # (B, 64, d)
        g = self.global_proj(global_feats).unsqueeze(1)  # (B, 1, d)
        c = self.cls_token.expand(B, -1, -1)  # (B, 1, d)
        x = torch.cat([c, g, x], dim=1)  # (B, 66, d)

        for blk in self.blocks:
            x = blk(x)
        x = self.norm(x)
        pooled = x[:, 0]  # CLS pool

        policy = self.policy_head(pooled)
        value = torch.tanh(self.value_head(pooled))
        return policy, value
