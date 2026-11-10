"""Offline tests: synthetic SEC responses, real SQL transactions and chart output."""

import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib
matplotlib.use("Agg")
import fetch_data as fetch
import main as app


def observation(year, value, accession="original", filed="2025-02-25",
                start=None, end=None, form="10-K"):
    return {"start": start or f"{year}-01-01", "end": end or f"{year}-12-31",
            "val": value, "accn": accession, "filed": filed, "form": form}


def payload(cik="0000021344"):
    return {"cik": int(cik), "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [observation(2023, 1_000_000_000),
                                       observation(2024, 1_200_000_000)]}},
        "ProfitLoss": {"units": {"USD": [observation(2023, 150_000_000),
                                         observation(2024, 204_000_000)]}},
    }}}


def records_for_all(years=(2023, 2024)):
    result = []
    for ticker in fetch.COMPANIES:
        for year in years:
            result.append((ticker, {
                "year": year, "revenue": 1000.0 if year == years[0] else 1200.0,
                "net_income": 150.0 if year == years[0] else 204.0,
                "start": f"{year}-01-01", "end": f"{year}-12-31",
                "accession": "test", "filed": "2025-02-25", "revenue_tag": "Revenues",
            }))
    return result


class ExtractionTests(unittest.TestCase):
    def test_full_years_units_and_quarterly_filter(self):
        data = payload()
        for tag in ("Revenues", "ProfitLoss"):
            # A quarterly fact can appear in a 10-K and still is not annual.
            data["facts"]["us-gaap"][tag]["units"]["USD"].append(
                observation(2024, 999, start="2024-10-01"))
        result = fetch.annual_records(data)
        self.assertEqual(result[2024]["revenue"], 1200)
        self.assertEqual(result[2024]["net_income"], 204)

    def test_fiscal_calendar_and_53_week_year(self):
        data = payload()
        for tag in ("Revenues", "ProfitLoss"):
            values = data["facts"]["us-gaap"][tag]["units"]["USD"]
            values[:] = [observation(2022, 1_000_000, start="2021-12-26",
                                     end="2022-12-31")]
        self.assertIn(2022, fetch.annual_records(data))

    def test_latest_filing_selects_matched_restatement(self):
        data = payload()
        facts = data["facts"]["us-gaap"]
        facts["Revenues"]["units"]["USD"].append(
            observation(2024, 1_300_000_000, "amended", "2025-03-01", form="10-K/A"))
        facts["ProfitLoss"]["units"]["USD"].append(
            observation(2024, 210_000_000, "amended", "2025-03-01", form="10-K/A"))
        result = fetch.annual_records(data)[2024]
        self.assertEqual((result["revenue"], result["net_income"]), (1300, 210))
        self.assertEqual(result["accession"], "amended")

    def test_never_mix_reports_or_use_parent_only_profit(self):
        data = payload()
        facts = data["facts"]["us-gaap"]
        facts["ProfitLoss"]["units"]["USD"][1]["accn"] = "different"
        self.assertNotIn(2024, fetch.annual_records(data))
        facts["NetIncomeLoss"] = facts.pop("ProfitLoss")
        self.assertEqual(fetch.annual_records(data), {})

    def test_ambiguous_latest_values_are_unavailable(self):
        data = payload()
        facts = data["facts"]["us-gaap"]
        facts["Revenues"]["units"]["USD"].append(observation(2024, 777))
        result = fetch.annual_records(data)
        self.assertNotIn(2024, result)
        self.assertIn(2023, result)

    def test_matching_alternative_tags_and_periods(self):
        data = payload()
        facts = data["facts"]["us-gaap"]
        facts["SalesRevenueNet"] = deepcopy(facts["Revenues"])
        self.assertEqual(fetch.annual_records(data)[2024]["revenue"], 1200)
        facts["ProfitLoss"]["units"]["USD"][1]["start"] = "2023-12-31"
        self.assertNotIn(2024, fetch.annual_records(data))


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        fetch.initialise_database(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_six_records_no_duplicates_and_source_saved(self):
        fetch.save_records(self.connection, records_for_all())
        fetch.save_records(self.connection, records_for_all())
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM annual_financials").fetchone()[0], 6)
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM financial_sources").fetchone()[0], 6)
        self.assertTrue(app.has_complete_data(self.connection, (2023, 2024)))

    def test_rollback_if_later_record_fails(self):
        rows = records_for_all()
        rows[1] = ("UNKNOWN", rows[1][1])
        with self.assertRaises(ValueError):
            fetch.save_records(self.connection, rows)
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM annual_financials").fetchone()[0], 0)
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM financial_sources").fetchone()[0], 0)

    def test_unavailable_pair_preserves_existing_records(self):
        fetch.save_records(self.connection, records_for_all())
        with patch.object(fetch.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(fetch.DataUnavailable):
                fetch.update_years(self.connection, (1999, 2024), "test@example.com",
                                   downloader=lambda cik, email: payload(cik))
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM annual_financials").fetchone()[0], 6)

    def test_existing_schema_and_csv_does_not_overwrite_downloads(self):
        fetch.save_records(self.connection, records_for_all())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "financial_data.csv"
            path.write_text("ticker,year,revenue,net_income\nKO,2024,1,1\n")
            fetch.initialise_database(self.connection, path)
        self.assertEqual(self.connection.execute(
            "SELECT revenue FROM annual_financials WHERE year = 2024 LIMIT 1"
        ).fetchone()[0], 1200)


class ApplicationTests(unittest.TestCase):
    def test_year_input_validation_and_sorting(self):
        with patch("builtins.input", side_effect=["hello", "2024 2024", "2024,2018"]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fetch.choose_years(), (2018, 2024))

    def test_growth_handles_gaps_and_zero_negative_values(self):
        total, cagr = app.growth_rates(100, 200, 6)
        self.assertAlmostEqual(total, 100)
        self.assertAlmostEqual(cagr, 12.24620483)
        self.assertEqual(app.growth_rates(0, 100, 2), (None, None))
        self.assertIsNone(app.growth_rates(-100, 100, 2)[1])

    def test_analysis_filters_pair_and_chart_renders(self):
        connection = sqlite3.connect(":memory:")
        try:
            fetch.initialise_database(connection)
            fetch.save_records(connection, records_for_all((2018, 2024)))
            fetch.save_records(connection, records_for_all((2023, 2024)))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                app.analyse_company(connection.cursor(), "KO", (2018, 2024))
            self.assertNotIn("2023:", output.getvalue())
            self.assertIn("6 years", output.getvalue())
            with tempfile.TemporaryDirectory() as directory, \
                    patch.object(app, "BASE_DIR", Path(directory)), \
                    contextlib.redirect_stdout(io.StringIO()):
                image = app.compare_companies(connection.cursor(), (2018, 2024), show=False)
                self.assertGreater(image.stat().st_size, 1000)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
