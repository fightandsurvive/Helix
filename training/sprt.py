"""
Wald Sequential Probability Ratio Test.

Stops an engine-vs-engine match when statistical confidence is reached,
typically at 100-250 games instead of 1000+. Saves 50-80% of evaluation
compute over fixed-length matches.

H0: new_engine_elo <= elo0   (reject, promote nothing)
H1: new_engine_elo >= elo1   (accept, promote checkpoint)

Default (0, 5): "accept if we're at least 5 Elo stronger, reject if not."
The 5-Elo margin prevents promoting noise.
"""

from __future__ import annotations

import math


class SPRT:
    def __init__(
        self,
        elo0: float = 0.0,
        elo1: float = 5.0,
        alpha: float = 0.05,
        beta: float = 0.05,
    ):
        self.elo0 = elo0
        self.elo1 = elo1
        self.alpha = alpha
        self.beta = beta
        self.llr = 0.0
        self.upper = math.log((1 - beta) / alpha)
        self.lower = math.log(beta / (1 - alpha))
        self.w = self.l = self.d = 0

    @staticmethod
    def _elo_to_p(elo: float) -> float:
        """Elo → expected score via logistic conversion."""
        return 1.0 / (1.0 + 10 ** (-elo / 400.0))

    def _p0(self) -> float:
        return self._elo_to_p(self.elo0)

    def _p1(self) -> float:
        return self._elo_to_p(self.elo1)

    def update(self, result: float) -> None:
        """result: 1.0 win, 0.5 draw, 0.0 loss for the engine being tested."""
        p0, p1 = self._p0(), self._p1()
        if result == 1.0:
            self.llr += math.log(p1 / p0)
            self.w += 1
        elif result == 0.5:
            self.llr += math.log((1 - p1) / (1 - p0))
            self.d += 1
        else:
            self.llr += math.log((1 - p1) / (1 - p0))
            self.l += 1

    def status(self) -> str:
        if self.llr >= self.upper:
            return "h1_accepted"
        if self.llr <= self.lower:
            return "h0_accepted"
        return "running"
