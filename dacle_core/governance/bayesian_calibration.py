"""
Bayesian Calibration — Beta-Binomial posterior estimation for per-setup-type
win-rate priors. Feeds conviction scoring and position sizing with credible
intervals instead of point estimates.

Concrete Beta(α, β) priors calibrated from historical fill data per setup type.
Posterior updates via Beta-Binomial conjugate: Beta(α + wins, β + losses).

Usage:
    cal = BayesianCalibration()
    cal.update_from_fill("breakout", won=True)
    lo, hi = cal.posterior_credible_interval("breakout")  # 90% HDI
"""

import math
from typing import Dict, Optional, Tuple


SETUP_PRIORS: Dict[str, Tuple[float, float]] = {
    # (alpha, beta) — Beta distribution parameters
    # Alpha = pseudo-wins + 1, Beta = pseudo-losses + 1
    # Centered at alpha/(alpha+beta)
    "breakout":        (3.0, 3.0),  # 50% prior, moderate strength (6 pseudo-obs)
    "mean_reversion":  (4.0, 2.0),  # 67% prior — mean reversion edges exist in liquid markets
    "momentum":        (2.5, 3.5),  # 42% prior — momentum fades in choppy crypto
    "range_fade":      (3.5, 2.5),  # 58% prior — range-trading viable in sideways regimes
    "trend_follow":    (2.0, 4.0),  # 33% prior — trend-following hard in mean-reverting crypto
    "scalper":         (5.0, 5.0),  # 50% prior, stronger (10 pseudo-obs) — high frequency
    "generic":         (2.0, 2.0),  # 50% prior, weak (4 pseudo-obs) — unopinionated
}

DEFAULT_PRIOR = (2.0, 2.0)


class BayesianCalibration:
    """Beta-Binomial Bayesian calibration with per-setup-type priors.

    Tracks wins/losses per setup type in-memory (non-persistent — daemon
    lifetime only). For long-lived daemons, wire a periodic sync to Redis.
    """

    def __init__(self):
        self._counts: Dict[str, Tuple[float, float]] = {}
        # {setup_type: (total_wins, total_losses)}

    def update_from_fill(self, setup_type: str, won: bool) -> None:
        """Update posterior after a filled trade resolves."""
        w, l = self._counts.get(setup_type, (0.0, 0.0))
        if won:
            self._counts[setup_type] = (w + 1.0, l)
        else:
            self._counts[setup_type] = (w, l + 1.0)

    def posterior_params(self, setup_type: str) -> Tuple[float, float]:
        """Return posterior Beta(alpha, beta) for a setup type.

        posterior = Beta(prior_alpha + wins, prior_beta + losses)
        """
        prior_alpha, prior_beta = SETUP_PRIORS.get(setup_type, DEFAULT_PRIOR)
        wins, losses = self._counts.get(setup_type, (0.0, 0.0))
        return (prior_alpha + wins, prior_beta + losses)

    def posterior_mean(self, setup_type: str) -> float:
        """Posterior mean win probability."""
        alpha, beta = self.posterior_params(setup_type)
        if alpha + beta == 0:
            return 0.5
        return alpha / (alpha + beta)

    def posterior_credible_interval(
        self,
        setup_type: str,
        confidence: float = 0.90,
    ) -> Tuple[float, float]:
        """Highest-density interval (HDI) for the posterior win rate.

        Uses a grid approximation (not MCMC) — fast enough for hot-path use
        when called once per intent, not per-tick.

        Returns (lower, upper) bounds of the HDI.
        """
        alpha, beta = self.posterior_params(setup_type)

        # Grid approximation over [0.001, 0.999]
        n_grid = 200
        xs = [(i + 0.5) / n_grid for i in range(n_grid)]

        densities = []
        total = 0.0
        for x in xs:
            # Beta PDF: x^(α-1) * (1-x)^(β-1) / B(α,β)
            # Use log-space for numerical stability
            if x <= 0 or x >= 1:
                d = 0.0
            else:
                log_dens = (
                    (alpha - 1) * math.log(x)
                    + (beta - 1) * math.log(1 - x)
                    - _log_beta(alpha, beta)
                )
                d = math.exp(log_dens) if log_dens < 700 else float("inf")
            densities.append(d)
            total += d

        if total <= 0:
            return (0.0, 1.0)

        # Normalize and find HDI via sorted density descent
        points = [(xs[i], densities[i] / total) for i in range(n_grid)]
        points.sort(key=lambda p: p[1], reverse=True)

        cum = 0.0
        hdi_points = []
        for x, d in points:
            if cum >= confidence:
                break
            hdi_points.append(x)
            cum += d

        if not hdi_points:
            return (0.0, 1.0)

        return (min(hdi_points), max(hdi_points))

    def get_stats(self, setup_type: str) -> dict:
        """Return calibration stats for debugging/dashboard use."""
        alpha, beta = self.posterior_params(setup_type)
        lo, hi = self.posterior_credible_interval(setup_type)
        wins, losses = self._counts.get(setup_type, (0.0, 0.0))
        prior = SETUP_PRIORS.get(setup_type, DEFAULT_PRIOR)

        return {
            "setup_type": setup_type,
            "prior_alpha": prior[0],
            "prior_beta": prior[1],
            "observed_wins": int(wins),
            "observed_losses": int(losses),
            "posterior_alpha": round(alpha, 2),
            "posterior_beta": round(beta, 2),
            "posterior_mean": round(self.posterior_mean(setup_type), 4),
            "hdi_90_lower": round(lo, 4),
            "hdi_90_upper": round(hi, 4),
            "hdi_width": round(hi - lo, 4),
        }


def _log_beta(a: float, b: float) -> float:
    """Log of Beta function: log Γ(a) + log Γ(b) - log Γ(a+b)."""
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
