"""Iterative proportional fitting (raking).

The mathematical heart of the calibrated generator. Given a seed matrix and target
row/column marginals, IPF alternately rescales rows then columns until the matrix
satisfies *both* sets of totals at once. We use it to fit a synthetic
customer × product revenue table so that customer totals tie to the segment
marginals and product totals tie to the gross-margin constraint — i.e. the
generated ledger sums back to the company's real reported numbers, by construction.
"""

from __future__ import annotations

import numpy as np


def ipf(seed: np.ndarray, row_targets: np.ndarray, col_targets: np.ndarray,
        *, max_iter: int = 200, tol: float = 1e-6) -> np.ndarray:
    """Rake `seed` to the given row and column marginals.

    row_targets and col_targets must sum to (approximately) the same grand total.
    Zero rows/cols are left at zero. Returns a fitted copy.
    """
    X = np.asarray(seed, dtype=float).copy()
    row_targets = np.asarray(row_targets, dtype=float)
    col_targets = np.asarray(col_targets, dtype=float)
    if X.shape != (len(row_targets), len(col_targets)):
        raise ValueError("seed shape must match (rows, cols) of the targets")

    for _ in range(max_iter):
        rs = X.sum(axis=1)
        scale_r = np.divide(row_targets, rs, out=np.ones_like(rs), where=rs > 0)
        X *= scale_r[:, None]
        cs = X.sum(axis=0)
        scale_c = np.divide(col_targets, cs, out=np.ones_like(cs), where=cs > 0)
        X *= scale_c[None, :]
        # converged when both marginals are matched
        if (np.max(np.abs(X.sum(axis=1) - row_targets)) < tol * max(row_targets.sum(), 1)
                and np.max(np.abs(X.sum(axis=0) - col_targets)) < tol * max(col_targets.sum(), 1)):
            break
    return X


def rake_1d(weights: np.ndarray, target_total: float) -> np.ndarray:
    """Scale non-negative weights so they sum exactly to target_total."""
    w = np.asarray(weights, dtype=float)
    s = w.sum()
    if s <= 0:
        return np.full_like(w, target_total / max(len(w), 1))
    return w * (target_total / s)


def solve_margin_shares(margins: np.ndarray, target_blended_margin: float,
                        base_shares: np.ndarray | None = None,
                        *, max_iter: int = 200) -> np.ndarray:
    """Find product revenue shares (sum to 1) whose margin-weighted average equals
    target_blended_margin, staying as close as possible to base_shares. Shifts weight
    between high- and low-margin products via a 1-D exponential tilt (a logit rake)."""
    m = np.asarray(margins, dtype=float)
    s = (np.asarray(base_shares, dtype=float) if base_shares is not None
         else np.full(len(m), 1.0 / len(m)))
    s = s / s.sum()
    lo, hi = -50.0, 50.0
    for _ in range(max_iter):  # bisection on a tilt parameter t: share ∝ s*exp(t*m)
        t = (lo + hi) / 2
        w = s * np.exp(t * (m - m.mean()))
        w = w / w.sum()
        blended = float((w * m).sum())
        if abs(blended - target_blended_margin) < 1e-9:
            return w
        if blended < target_blended_margin:
            lo = t
        else:
            hi = t
    return w
