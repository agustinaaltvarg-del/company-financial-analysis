"""Download comparable annual SEC figures and save a selected pair of years."""

import csv
import json
import math
import sqlite3
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "financial_analysis.db"
COMPANIES = {
    "KO": {"name": "Coca-Cola", "cik": "0000021344"},
    "PEP": {"name": "PepsiCo", "cik": "0000077476"},
    "KDP": {"name": "Keurig Dr Pepper", "cik": "0001418135"},
}
REVENUE_TAGS = (
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)


class DataUnavailable(ValueError):
    """The requested years cannot be compared safely for every company."""


def initialise_database(connection, csv_path=None):
    """Keep the existing schema and optionally seed missing CSV records."""
    connection.execute("PRAGMA foreign_keys = ON")
    with connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                ticker TEXT NOT NULL UNIQUE
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS annual_financials (
                company_id INTEGER NOT NULL,
                year INTEGER NOT NULL,
                revenue REAL NOT NULL,
                net_income REAL NOT NULL,
                PRIMARY KEY (company_id, year),
                FOREIGN KEY (company_id) REFERENCES companies(id)
            )
        """)
        # Keep evidence separately without changing the user's existing table.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS financial_sources (
                company_id INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                accession TEXT NOT NULL,
                filed TEXT NOT NULL,
                revenue_tag TEXT NOT NULL,
                downloaded_at TEXT NOT NULL,
                PRIMARY KEY (company_id, year),
                FOREIGN KEY (company_id, year)
                    REFERENCES annual_financials(company_id, year)
            )
        """)
        for ticker, company in COMPANIES.items():
            connection.execute(
                "INSERT OR IGNORE INTO companies (name, ticker) VALUES (?, ?)",
                (company["name"], ticker),
            )
        if csv_path is not None and Path(csv_path).is_file():
            with open(csv_path, newline="", encoding="utf-8-sig") as file:
                for row in csv.DictReader(file):
                    ticker = row["ticker"].strip().upper()
                    if ticker not in COMPANIES:
                        raise ValueError(f"Unknown CSV ticker: {ticker}")
                    revenue = float(row["revenue"])
                    net_income = float(row["net_income"])
                    if not all(map(math.isfinite, (revenue, net_income))):
                        raise ValueError("CSV amounts must be finite numbers.")
                    company_id = connection.execute(
                        "SELECT id FROM companies WHERE ticker = ?", (ticker,)
                    ).fetchone()[0]
                    connection.execute("""
                        INSERT OR IGNORE INTO annual_financials
                            (company_id, year, revenue, net_income)
                        VALUES (?, ?, ?, ?)
                    """, (company_id, int(row["year"]), revenue, net_income))


def choose_years():
    """Prompt until two different, plausible year numbers are supplied."""
    while True:
        text = input("Enter two years, e.g. 2018 2024 (Q to cancel): ").strip()
        if text.upper() == "Q":
            return None
        try:
            years = tuple(sorted(int(part) for part in text.replace(",", " ").split()))
            if (len(years) != 2 or years[0] == years[1]
                    or years[0] < 1900 or years[1] > date.today().year):
                raise ValueError
            return years
        except ValueError:
            print("Enter two different years between 1900 and the current year.")


def download_company(cik, email):
    request = Request(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
        headers={
            "User-Agent": f"CompanyFinancialAnalysis/2.0 {email}",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def annual_observations(facts, tag):
    """Index full-year USD facts by filing AND exact accounting period."""
    indexed = {}
    for item in facts.get(tag, {}).get("units", {}).get("USD", []):
        if item.get("form") not in ("10-K", "10-K/A"):
            continue
        try:
            start = date.fromisoformat(item["start"])
            end = date.fromisoformat(item["end"])
            filed = date.fromisoformat(item["filed"])
            value = float(item["val"])
            accession = item["accn"]
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if (350 <= (end - start).days <= 380
                and end <= date.today() and filed <= date.today()
                and math.isfinite(value)):
            key = (filed.isoformat(), accession, start.isoformat(), end.isoformat())
            indexed.setdefault(key, set()).add(value)
    return indexed


def annual_records(data):
    """Choose the latest matched filing for each period-end calendar year.

    These three companies have December year ends. ProfitLoss is consolidated
    net income; never substitute shareholder-only NetIncomeLoss silently.
    """
    facts = data.get("facts", {}).get("us-gaap", {})
    income = annual_observations(facts, "ProfitLoss")
    candidates = {}
    for priority, tag in enumerate(REVENUE_TAGS):
        revenue = annual_observations(facts, tag)
        for key in revenue.keys() & income.keys():
            filed, accession, start, end = key
            row = {
                "year": date.fromisoformat(end).year,
                "revenue_values": revenue[key], "income_values": income[key],
                "start": start, "end": end, "filed": filed,
                "accession": accession, "revenue_tag": tag,
            }
            candidates.setdefault(row["year"], []).append((key, -priority, row))
    result = {}
    for year, options in candidates.items():
        latest_key = max(option[0] for option in options)
        latest = [option for option in options if option[0] == latest_key]
        # Ambiguous years are unavailable; never select a convenient older value.
        revenue_values = set().union(*(option[2]["revenue_values"] for option in latest))
        income_values = set().union(*(option[2]["income_values"] for option in latest))
        if len(revenue_values) != 1 or len(income_values) != 1:
            continue
        row = max(latest, key=lambda option: option[1])[2].copy()
        row["revenue"] = revenue_values.pop() / 1_000_000
        row["net_income"] = income_values.pop() / 1_000_000
        del row["revenue_values"], row["income_values"]
        result[year] = row
    return result


def save_records(connection, records):
    """Save amounts and their source evidence in a single transaction."""
    downloaded_at = datetime.now(timezone.utc).isoformat()
    with connection:
        for ticker, row in records:
            company = connection.execute(
                "SELECT id FROM companies WHERE ticker = ?", (ticker,)
            ).fetchone()
            if company is None:
                raise ValueError(f"Unknown company: {ticker}")
            company_id = company[0]
            connection.execute("""
                INSERT INTO annual_financials
                    (company_id, year, revenue, net_income)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(company_id, year) DO UPDATE SET
                    revenue = excluded.revenue, net_income = excluded.net_income
            """, (company_id, row["year"], row["revenue"], row["net_income"]))
            connection.execute("""
                INSERT INTO financial_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, year) DO UPDATE SET
                    period_start = excluded.period_start,
                    period_end = excluded.period_end,
                    accession = excluded.accession, filed = excluded.filed,
                    revenue_tag = excluded.revenue_tag,
                    downloaded_at = excluded.downloaded_at
            """, (company_id, row["year"], row["start"], row["end"],
                  row["accession"], row["filed"], row["revenue_tag"], downloaded_at))


def update_years(connection, years, email, downloader=download_company):
    """Require all three companies for both years; preserve DB on failure."""
    records = []
    missing = []
    available_sets = []
    for ticker, company in COMPANIES.items():
        print(f"Downloading {ticker}...")
        data = downloader(company["cik"], email)
        if int(data.get("cik", -1)) != int(company["cik"]):
            raise DataUnavailable(f"Unexpected company in SEC response for {ticker}.")
        available = annual_records(data)
        available_sets.append(set(available))
        print(f"  Supported years: {', '.join(map(str, sorted(available))) or 'none'}")
        for year in years:
            if year not in available:
                missing.append(f"{ticker}: {year}")
            else:
                records.append((ticker, available[year]))
        time.sleep(0.2)
    if missing:
        common = sorted(set.intersection(*available_sets))
        raise DataUnavailable(
            "Missing supported annual data for " + "; ".join(missing)
            + ".\nYears supported for ALL three: "
            + (", ".join(map(str, common)) or "none")
            + ".\nNo financial records were changed. Choose another pair."
        )
    save_records(connection, records)
    print(f"Saved {len(records)} annual records. Amounts are in USD millions.")


def main():
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        initialise_database(connection)
        years = choose_years()
        if years is not None:
            email = input("Contact email sent to the SEC: ").strip()
            if not email or "@" not in email:
                raise ValueError("Enter a valid contact email.")
            update_years(connection, years, email)
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
    except Exception as error:
        print(f"Update failed: {error}")
