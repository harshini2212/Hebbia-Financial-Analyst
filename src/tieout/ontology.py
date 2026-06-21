"""Canonical concept space + us-gaap tag mapping.

This is the dictionary that makes "segments sum to total" verifiable: both the
XBRL `us-gaap` tags and free-text LLM extractions must resolve to the *same*
canonical concept id, or no identity can be checked across sources.

Phase 1 splits the NCI-sensitive concepts (equity, net income) so the parent vs
consolidated distinction is explicit — a real source of accounting error. The
mapping is data, not logic — the highest-leverage artifact in the system.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Statement(str, Enum):
    BALANCE_SHEET = "balance_sheet"
    INCOME = "income"
    CASH_FLOW = "cash_flow"
    DERIVED = "derived"


class DataType(str, Enum):
    MONETARY = "monetary"
    SHARES = "shares"
    RATIO = "ratio"
    PER_SHARE = "per_share"
    PURE = "pure"


class PeriodType(str, Enum):
    INSTANT = "instant"
    DURATION = "duration"


@dataclass(frozen=True)
class Concept:
    """One node in the canonical concept space."""

    id: str
    label: str
    statement: Statement
    data_type: DataType
    period_type: PeriodType
    dimensional: bool = False  # can it be sliced by segment/geography?
    gaap_tags: tuple[str, ...] = ()  # us-gaap tags that map here (many-to-one)
    aliases: tuple[str, ...] = ()  # text-extraction surface forms
    positive_is_natural: bool = True


def _M(id, label, tags, aliases=()):  # balance-sheet monetary instant
    return Concept(id, label, Statement.BALANCE_SHEET, DataType.MONETARY,
                   PeriodType.INSTANT, gaap_tags=tags, aliases=aliases)


def _I(id, label, tags, aliases=(), dimensional=False):  # income monetary duration
    return Concept(id, label, Statement.INCOME, DataType.MONETARY,
                   PeriodType.DURATION, dimensional=dimensional, gaap_tags=tags,
                   aliases=aliases)


def _R(id, label, aliases=()):  # derived ratio (no native gaap tag)
    return Concept(id, label, Statement.DERIVED, DataType.RATIO,
                   PeriodType.DURATION, aliases=aliases)


_CONCEPTS: tuple[Concept, ...] = (
    # --- Balance sheet ---
    _M("assets.total", "Total assets", ("Assets",), ("total assets",)),
    _M("assets.current", "Total current assets", ("AssetsCurrent",), ("total current assets",)),
    _M("assets.noncurrent", "Total non-current assets", ("AssetsNoncurrent",)),
    # Working-capital lines — constraints for the synthetic ledger (DSO/DIO/DPO).
    _M("accounts_receivable.total", "Accounts receivable, net",
       ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"), ("accounts receivable",)),
    _M("inventory.total", "Inventory, net", ("InventoryNet",), ("inventories", "inventory")),
    _M("accounts_payable.total", "Accounts payable, current",
       ("AccountsPayableCurrent", "AccountsPayableTradeCurrent"), ("accounts payable",)),
    _M("liabilities.total", "Total liabilities", ("Liabilities",), ("total liabilities",)),
    _M("liabilities.current", "Total current liabilities", ("LiabilitiesCurrent",),
       ("total current liabilities",)),
    _M("liabilities.noncurrent", "Total non-current liabilities", ("LiabilitiesNoncurrent",)),
    # Equity: parent vs consolidated (incl. NCI) kept distinct.
    _M("equity.total", "Total equity incl. noncontrolling interest",
       ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",),
       ("total equity",)),
    _M("equity.parent", "Stockholders' equity attributable to parent",
       ("StockholdersEquity",), ("total stockholders equity",)),
    _M("equity.nci", "Noncontrolling interest", ("MinorityInterest",),
       ("noncontrolling interest",)),
    _M("equity.temporary", "Redeemable / temporary (mezzanine) equity",
       ("RedeemableNoncontrollingInterestEquityCarryingAmount",
        "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
        "TemporaryEquityCarryingAmountAttributableToParent"),
       ("redeemable noncontrolling interest", "temporary equity")),
    _M("liabilities_and_equity.total", "Total liabilities and equity",
       ("LiabilitiesAndStockholdersEquity",)),

    # --- Income statement ---
    _I("revenue.total", "Total revenue",
       ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"),
       ("total revenue", "net sales", "total net sales", "revenues")),
    _I("revenue.segment", "Segment revenue",
       ("RevenueFromContractWithCustomerExcludingAssessedTax",), dimensional=True),
    _I("cogs.total", "Cost of goods/revenue",
       ("CostOfGoodsAndServicesSold", "CostOfRevenue"),
       ("cost of sales", "cost of revenue", "cost of goods sold")),
    _I("gross_profit.total", "Gross profit", ("GrossProfit",), ("gross profit",)),
    _I("opex.total", "Total operating expenses", ("OperatingExpenses", "CostsAndExpenses"),
       ("total operating expenses",)),
    _I("operating_income.total", "Operating income", ("OperatingIncomeLoss",),
       ("operating income", "income from operations")),
    _I("operating_income.segment", "Segment operating income", (), dimensional=True),
    # Net income: parent vs consolidated (incl. NCI) kept distinct.
    _I("net_income.consolidated", "Consolidated net income (incl. NCI)", ("ProfitLoss",),
       ("consolidated net income",)),
    _I("net_income.parent", "Net income attributable to parent", ("NetIncomeLoss",),
       ("net income", "net earnings")),
    _I("net_income.nci", "Net income attributable to noncontrolling interest",
       ("NetIncomeLossAttributableToNoncontrollingInterest",)),
    _I("pretax_income.total", "Income before income taxes",
       ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"),
       ("income before taxes", "pretax income")),
    _I("income_tax.total", "Income tax expense", ("IncomeTaxExpenseBenefit",),
       ("income tax expense", "provision for income taxes")),
    _I("income.equity_method", "Equity-method investment income/loss",
       ("IncomeLossFromEquityMethodInvestmentsNetOfTax",
        "IncomeLossFromEquityMethodInvestments"),
       ("equity method investment", "equity in earnings")),
    _I("income.discontinued", "Income/loss from discontinued operations, net of tax",
       ("IncomeLossFromDiscontinuedOperationsNetOfTax",
        "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToReportingEntity"),
       ("discontinued operations",)),

    # --- Cash flow (minimal) ---
    Concept("cfo.total", "Net cash from operating activities", Statement.CASH_FLOW,
            DataType.MONETARY, PeriodType.DURATION,
            gaap_tags=("NetCashProvidedByUsedInOperatingActivities",)),
    Concept("capex.total", "Capital expenditures (PP&E)", Statement.CASH_FLOW,
            DataType.MONETARY, PeriodType.DURATION,
            gaap_tags=("PaymentsToAcquirePropertyPlantAndEquipment",
                       "PaymentsToAcquireProductiveAssets")),

    # --- Derived ratios ---
    _R("gross_margin.ratio", "Gross margin", ("gross margin",)),
    _R("operating_margin.ratio", "Operating margin", ("operating margin",)),
    _R("net_margin.ratio", "Net margin", ("net margin", "net profit margin")),
    _R("effective_tax_rate.ratio", "Effective tax rate", ("effective tax rate",)),
)

ONTOLOGY: dict[str, Concept] = {c.id: c for c in _CONCEPTS}

# Reverse index: us-gaap tag -> canonical concept id (first match wins).
_GAAP_INDEX: dict[str, str] = {}
for _c in _CONCEPTS:
    for _tag in _c.gaap_tags:
        _GAAP_INDEX.setdefault(_tag, _c.id)


def concept(concept_id: str) -> Concept:
    try:
        return ONTOLOGY[concept_id]
    except KeyError as exc:  # pragma: no cover - guardrail
        raise KeyError(f"unknown concept id {concept_id!r}; not in ontology") from exc


def concept_for_gaap_tag(tag: str) -> str | None:
    return _GAAP_INDEX.get(tag)
