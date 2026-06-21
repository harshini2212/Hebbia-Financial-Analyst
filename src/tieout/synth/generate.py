"""The calibrated synthetic ERP/CRM generator.

Produces a realistic private ledger (customers, products, revenue lines, AR aging)
for a company, constrained so it sums back to that company's real reported XBRL
figures. Not random fake data and not real customer data — *calibrated demo data*.

Mechanics: customer sizes are drawn from a heavy-tailed (log-normal) distribution
and raked (1-D IPF) to the real segment revenue; products carry unit margins that
are fit to the reported gross margin; the customer × product table is fit with 2-D
IPF so both marginals hold at once. Working capital (AR aging) is generated to
reproduce the balance-sheet-derived DSO. Everything is seeded from the CIK, so the
same company always yields the same ledger.

Planted, findable anomalies (what diligence actually looks for): a concentration
whale whose share grows YoY (so *reported* growth overstates *underlying* growth),
a margin-eroding product line gaining share, one-time revenue inflating the top
line, and a DSO drift (collections slowing).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

from .constraints import CompanyConstraints, PeriodConstraints
from .ipf import ipf, rake_1d, solve_margin_shares

_STEMS = ("Atlas", "Northwind", "Vertex", "Cobalt", "Summit", "Granite", "Beacon",
          "Pinnacle", "Harbor", "Meridian", "Cedar", "Orion", "Quanta", "Ironclad",
          "Brightwave", "Sterling", "Kestrel", "Lumen", "Cascade", "Sequoia",
          "Vanguard", "Monarch", "Aspen", "Cinder", "Halcyon", "Onyx", "Dovetail")
_SUFFIX = ("Corp", "Labs", "Holdings", "Systems", "Group", "Industries", "Partners",
           "Logistics", "Health", "Capital", "Retail", "Foods", "Networks", "Energy")
_GEOS = ("North America", "EMEA", "APAC", "Latin America")
_PRODUCTS = ("Platform", "Core Suite", "Analytics", "Services", "Legacy")


@dataclass
class Customer:
    id: str
    name: str
    segment: str
    geo: str
    cohort_year: int
    is_whale: bool = False


@dataclass
class Product:
    id: str
    name: str
    unit_margin: float


@dataclass
class RevenueLine:
    customer_id: str
    product_id: str
    fiscal_year: int
    amount: float
    recurring: bool


@dataclass
class ARInvoice:
    customer_id: str
    fiscal_year: int
    amount: float
    aging_bucket: str  # "0-30" | "31-60" | "61-90" | "90+"


@dataclass
class SyntheticLedger:
    ticker: str
    issuer: str
    cik: str
    years: list[int]
    customers: list[Customer]
    products: list[Product]
    revenue_lines: list[RevenueLine]
    ar_invoices: list[ARInvoice]
    anomalies: list[dict] = field(default_factory=list)

    def customer(self, cid: str) -> Customer:
        return self._cmap[cid]

    def __post_init__(self):
        self._cmap = {c.id: c for c in self.customers}


_AGING = ("0-30", "31-60", "61-90", "90+")
_AGING_MID = {"0-30": 15, "31-60": 45, "61-90": 75, "90+": 110}


def generate(cons: CompanyConstraints, *, n_customers: int = 140) -> SyntheticLedger:
    seed = int(hashlib.sha256(cons.cik.encode()).hexdigest()[:12], 16)
    rng = np.random.default_rng(seed)

    years = sorted(p.fiscal_year for p in cons.periods if p.revenue_total)
    by_year = cons.by_year()
    latest = by_year[years[-1]]

    # --- segments (fall back to a single synthetic segment) ---
    segs = latest.segments or {"Total": latest.revenue_total}
    seg_names = list(segs)
    seg_rev = np.array([segs[s] for s in seg_names], dtype=float)
    largest_seg = seg_names[int(seg_rev.argmax())]

    # --- customer base: split N across segments by revenue share ---
    shares = seg_rev / seg_rev.sum()
    counts = np.maximum(2, np.round(shares * n_customers).astype(int))
    customers: list[Customer] = []
    weights: dict[str, float] = {}
    idx = 0
    for s, cnt in zip(seg_names, counts):
        w = rng.lognormal(mean=0.0, sigma=1.15, size=cnt)
        for k in range(cnt):
            cohort = years[0] - 1 if rng.random() < 0.7 else int(rng.choice(years))
            cid = f"C{idx:04d}"
            customers.append(Customer(cid, _name(rng, idx), s, _GEOS[idx % len(_GEOS)], cohort))
            weights[cid] = float(w[k])
            idx += 1

    # plant the whale: the biggest customer in the largest segment
    whale = max((c for c in customers if c.segment == largest_seg),
                key=lambda c: weights[c.id])
    whale.is_whale = True

    # --- products: margins bracketing the blended gross margin ---
    gm = latest.gross_margin if latest.gross_margin is not None else 0.40
    spread = np.array([0.16, 0.08, 0.0, -0.10, -0.20])  # relative to blended
    margins = np.clip(gm + spread, 0.02, 0.95)
    products = [Product(f"P{j}", _PRODUCTS[j], float(margins[j])) for j in range(len(_PRODUCTS))]
    erode_idx = int(margins.argmin())  # the margin-eroding line (lowest margin)

    # whale share of its segment grows YoY -> reported growth overstates underlying
    whale_share = {y: 0.15 + 0.03 * i for i, y in enumerate(years)}

    rev_lines: list[RevenueLine] = []
    ar_invoices: list[ARInvoice] = []
    whale_rev_by_year: dict[int, float] = {}
    onetime_by_year: dict[int, float] = {}
    prod_share_by_year: dict[int, np.ndarray] = {}

    for i, y in enumerate(years):
        pc = by_year[y]
        if not pc.revenue_total:
            continue
        active = [c for c in customers if c.cohort_year <= y]
        # ---- per-segment customer revenue (rake to the real segment marginal) ----
        cust_total: dict[str, float] = {}

        def assign(members, target):
            if not members or target <= 0:
                return
            wh = next((c for c in members if c.is_whale), None)
            if wh is not None:
                wr = min(whale_share[y] * target, 0.55 * target)
                whale_rev_by_year[y] = wr
                cust_total[wh.id] = wr
                rest = [c for c in members if c is not wh]
                if rest:
                    rw = rake_1d(np.array([weights[c.id] for c in rest]), target - wr)
                    for c, v in zip(rest, rw):
                        cust_total[c.id] = float(v)
            else:
                aw = rake_1d(np.array([weights[c.id] for c in members]), target)
                for c, v in zip(members, aw):
                    cust_total[c.id] = float(v)

        if pc.segments:
            targets = {s: pc.segments[s] for s in seg_names if pc.segments.get(s)}
            assigned: set[str] = set()
            for s, target in targets.items():
                members = [c for c in active if c.segment == s]
                assign(members, target)
                assigned |= {c.id for c in members}
            rest = [c for c in active if c.id not in assigned]
            residual = pc.revenue_total - sum(cust_total.values())
            if rest and residual > 0:
                assign(rest, residual)
        else:
            assign(active, pc.revenue_total)

        # ---- products: shares fit to gross margin, eroding line drifts up ----
        base = np.full(len(products), 1.0 / len(products))
        base[erode_idx] += 0.05 * i  # the low-margin line gains share over time
        base = base / base.sum()
        gmy = pc.gross_margin if pc.gross_margin is not None else gm
        p_shares = solve_margin_shares(margins, gmy, base)
        prod_share_by_year[y] = p_shares
        col_targets = p_shares * pc.revenue_total

        # ---- 2-D IPF: customer x product, both marginals hold ----
        cids = [c.id for c in active if c.id in cust_total]
        row_targets = np.array([cust_total[cid] for cid in cids])
        row_targets = rake_1d(row_targets, pc.revenue_total)  # ensure totals match exactly
        seed_mat = np.outer(row_targets / row_targets.sum(), p_shares)
        seed_mat *= (1 + 0.15 * rng.standard_normal(seed_mat.shape))
        seed_mat = np.clip(seed_mat, 1e-9, None)
        X = ipf(seed_mat, row_targets, col_targets)

        onetime = 0.0
        for r, cid in enumerate(cids):
            for j, prod in enumerate(products):
                amt = float(X[r, j])
                if amt < 1000:
                    continue
                # the whale's incremental growth is partly one-time (inflates the top line)
                recurring = True
                if customers and self_is_whale(customers, cid) and j == 0 and i == len(years) - 1:
                    recurring = False
                    onetime += amt * 0.45
                rev_lines.append(RevenueLine(cid, prod.id, y, amt, recurring))
        onetime_by_year[y] = onetime

        # ---- AR aging: total ties to reported AR, weighted age ~ DSO ----
        if pc.accounts_receivable and pc.dso():
            _gen_ar(rng, cids, cust_total, pc, whale.id if i == len(years) - 1 else None,
                    ar_invoices)

    anomalies = _anomalies(cons, years, whale, products[erode_idx], whale_rev_by_year,
                           onetime_by_year, by_year)
    return SyntheticLedger(cons.ticker, cons.issuer, cons.cik, years, customers,
                           products, rev_lines, ar_invoices, anomalies)


def self_is_whale(customers, cid) -> bool:
    for c in customers:
        if c.id == cid:
            return c.is_whale
    return False


def _gen_ar(rng, cids, cust_total, pc: PeriodConstraints, drift_whale_id, out):
    ar_total = pc.accounts_receivable
    dso = pc.dso()
    revs = np.array([cust_total.get(cid, 0.0) for cid in cids])
    if revs.sum() <= 0:
        return
    cust_ar = rake_1d(revs, ar_total)
    # target weighted average age = DSO; bias older if collections slow
    for cid, ar in zip(cids, cust_ar):
        if ar < 1000:
            continue
        # base distribution centered to hit the DSO
        center = dso * 1.0
        if drift_whale_id and cid == drift_whale_id:
            center = dso * 1.6  # the whale is paying slowly -> DSO drift
        w = np.array([_bucket_weight(_AGING_MID[b], center) for b in _AGING])
        w = w / w.sum()
        for b, frac in zip(_AGING, w):
            amt = float(ar * frac)
            if amt > 500:
                out.append(ARInvoice(cid, pc.fiscal_year, amt, b))


def _bucket_weight(mid: float, center: float) -> float:
    return float(np.exp(-((mid - center) ** 2) / (2 * (center * 0.7 + 1) ** 2)))


def _anomalies(cons, years, whale, erode_product, whale_rev, onetime, by_year) -> list[dict]:
    out = []
    latest = years[-1]
    seg = whale.segment
    seg_rev = (by_year[latest].segments.get(seg) if by_year[latest].segments
               else by_year[latest].revenue_total) or 0.0
    if seg_rev and whale_rev.get(latest):
        out.append({"type": "concentration", "severity": "high",
                    "customer": whale.name, "customer_id": whale.id,
                    "pct_of_segment": round(whale_rev[latest] / seg_rev, 4),
                    "segment": seg})
    if onetime.get(latest):
        out.append({"type": "one_time_revenue", "severity": "medium",
                    "amount": round(onetime[latest]), "note": "one-time revenue inflating the latest period"})
    out.append({"type": "margin_erosion", "severity": "medium",
                "product": erode_product.name, "unit_margin": round(erode_product.unit_margin, 3),
                "note": "lowest-margin line gaining revenue share YoY"})
    # DSO drift if AR present for two periods
    if len(years) >= 2:
        a, b = by_year[years[-1]], by_year[years[-2]]
        if a.dso() and b.dso():
            out.append({"type": "dso_drift", "severity": "medium",
                        "dso_now": round(a.dso(), 1), "dso_prior": round(b.dso(), 1),
                        "delta_days": round(a.dso() - b.dso(), 1)})
    return out


def _name(rng, idx: int) -> str:
    return f"{_STEMS[idx % len(_STEMS)]} {_SUFFIX[(idx // len(_STEMS)) % len(_SUFFIX)]}"
