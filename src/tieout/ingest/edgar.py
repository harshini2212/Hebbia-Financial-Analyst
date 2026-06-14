"""Locate SEC filings on EDGAR.

Resolves a ticker -> CIK and finds 10-K filings (latest or a specific fiscal
year), returning a FilingLocator that points at the primary inline-XBRL document.
SEC fair-access requires a descriptive User-Agent with contact info; callers pass
one in (we default to the project contact).
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{doc}"

DEFAULT_USER_AGENT = "tieout-research hv2201@nyu.edu"

# Brand/product → ticker, for searches that won't match the legal entity name.
_ALIASES = {
    "google": "GOOGL", "alphabet": "GOOGL", "youtube": "GOOGL",
    "facebook": "META", "instagram": "META", "whatsapp": "META",
    "nbc": "CMCSA", "universal": "CMCSA", "aws": "AMZN",
    "chatgpt": "MSFT", "windows": "MSFT", "xbox": "MSFT",
    "iphone": "AAPL", "instagram": "META",
}


@dataclass(frozen=True)
class FilingLocator:
    issuer: str
    cik: str  # zero-padded 10-digit
    ticker: str
    accession: str  # dashed form, e.g. 0000909832-25-000101
    primary_doc: str  # e.g. cost-20250831.htm (inline XBRL)
    filing_date: str
    fiscal_year: int  # DocumentFiscalYearFocus from the index
    form: str = "10-K"

    @property
    def url(self) -> str:
        return _ARCHIVE_URL.format(
            cik=int(self.cik),
            acc_nodash=self.accession.replace("-", ""),
            doc=self.primary_doc,
        )


class EdgarClient:
    def __init__(self, user_agent: str = DEFAULT_USER_AGENT, timeout: int = 30) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self._ticker_index: dict[str, tuple[str, str]] | None = None
        self._companies: list[dict] | None = None

    def _get(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read()

    def _load_ticker_index(self) -> dict[str, tuple[str, str]]:
        if self._ticker_index is None:
            raw = json.loads(self._get(_TICKERS_URL))
            # raw is {idx: {cik_str, ticker, title}}
            self._ticker_index = {
                row["ticker"].upper(): (f"{int(row['cik_str']):010d}", row["title"])
                for row in raw.values()
            }
        return self._ticker_index

    def _load_companies(self) -> list[dict]:
        if self._companies is None:
            raw = json.loads(self._get(_TICKERS_URL))
            # raw is dict ordered by descending market cap; keep that as `rank`.
            self._companies = [
                {"ticker": r["ticker"].upper(), "name": r["title"],
                 "cik": f"{int(r['cik_str']):010d}", "rank": i}
                for i, r in enumerate(raw.values())
            ]
        return self._companies

    def search(self, query: str, limit: int = 8) -> list[dict]:
        """Typeahead over EDGAR companies, ranked by match quality then size."""
        q = query.strip().lower()
        if not q:
            return []
        alias = _ALIASES.get(q)
        scored = []
        for c in self._load_companies():
            t, name = c["ticker"].lower(), c["name"].lower()
            if t == q:
                s = 1000
            elif t.startswith(q):
                s = 720
            elif name.startswith(q):
                s = 600
            elif (" " + q) in (" " + name):
                s = 380
            elif q in name:
                s = 220
            elif q in t:
                s = 150
            else:
                s = 0
            if alias and c["ticker"] == alias:
                s = max(s, 900)
            if s:
                scored.append((s, -c["rank"], c))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [{"ticker": c["ticker"], "name": c["name"]} for _, _, c in scored[:limit]]

    def resolve_cik(self, ticker: str) -> tuple[str, str]:
        """Return (cik10, issuer_name) for a ticker."""
        idx = self._load_ticker_index()
        try:
            return idx[ticker.upper()]
        except KeyError as exc:
            raise KeyError(f"ticker {ticker!r} not found in EDGAR index") from exc

    def find_10k(self, ticker: str, *, fiscal_year: int | None = None) -> FilingLocator:
        """Find a 10-K: the latest, or the one for a specific fiscal year."""
        cik, issuer = self.resolve_cik(ticker)
        sub = json.loads(self._get(_SUBMISSIONS_URL.format(cik10=cik)))
        recent = sub["filings"]["recent"]
        n = len(recent["form"])
        candidates = []
        for i in range(n):
            if recent["form"][i] != "10-K":
                continue
            fy = _fiscal_year_from_report_date(recent.get("reportDate", [""] * n)[i],
                                               recent["filingDate"][i])
            candidates.append(
                FilingLocator(
                    issuer=issuer,
                    cik=cik,
                    ticker=ticker.upper(),
                    accession=recent["accessionNumber"][i],
                    primary_doc=recent["primaryDocument"][i],
                    filing_date=recent["filingDate"][i],
                    fiscal_year=fy,
                )
            )
        if not candidates:
            raise LookupError(f"no 10-K found for {ticker!r}")
        if fiscal_year is None:
            return candidates[0]  # recent[] is newest-first
        for c in candidates:
            if c.fiscal_year == fiscal_year:
                return c
        raise LookupError(f"no 10-K for {ticker!r} fiscal year {fiscal_year}")


def _fiscal_year_from_report_date(report_date: str, filing_date: str) -> int:
    """Fiscal year = calendar year of the period-end (report) date."""
    src = report_date or filing_date
    return int(src[:4])
