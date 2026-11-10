# Company Financial Analysis

Compare Coca-Cola (KO), PepsiCo (PEP) and Keurig Dr Pepper (KDP) for two user-selected years using Python, SQL, SQLite and Matplotlib.

## Install this update

1. Keep a backup of your existing `main.py` and `fetch_data.py`.
2. Copy the two replacement `.py` files into your existing project folder, replacing those scripts. Keep BOTH scripts together: main.py imports functions from fetch_data.py.
3. Keep your existing `financial_analysis.db` and `financial_data.csv`. The database schema remains compatible, and an additional source-evidence table is created automatically.
4. Run `python -m pip install -r requirements.txt` if necessary. Matplotlib is the only external dependency.
5. In VS Code, open main.py and choose **Python: Run Python File in Terminal**.

You can alternatively extract the whole folder and start fresh. If no database or CSV exists, the application creates its own database and downloads the selected pair.

## Select years and run

At the first prompt, enter two distinct years, for example:

```text
2018 2024
```

This is an input example, not a guarantee of data availability. The application sorts the pair automatically. If any of the six required company/year records are missing locally, it requests a contact email and retrieves all three companies from the SEC. Your email is sent in the request header; the program does not save it.

If safe annual matches cannot be found for every company, it lists the unavailable company/year combinations and years supported by all three, without partially updating the financial records. Choose another pair. Company existence alone does not imply SEC data availability.

Menu:

| Command | Action |
| --- | --- |
| KO / PEP / KDP | Analyse the selected pair for one company |
| ALL | Compare all companies and show a grouped chart of both years |
| YEARS | Choose a different pair |
| UPDATE | Refresh the selected pair from SEC data |
| Q | Quit |

The main application reuses stored data when both years already exist for all companies. Use UPDATE to fetch current filing evidence and replace those records. Local or CSV-only records are labelled as such in individual company analysis.

Close the chart window to return to the terminal menu. PNG charts are saved alongside the scripts as `profit_margins_FIRSTYEAR_SECONDYEAR.png`. Repeating the same pair replaces that chart.

To update data without opening the analysis menu, run `python fetch_data.py`, select a pair, and enter your contact email.

## What changed and why

- A shared configuration holds the three companies; fixed report IDs and fixed comparison years have been removed.
- Main imports the downloader's reusable functions. Importing a module does not run its interactive menu because the entry point uses `if __name__ == "__main__"`.
- SQL `IN (?, ?)` restricts each analysis to the selected pair, even when the database contains other years.
- A grouped chart shows both years together and retains the original pink colour.
- Total revenue growth and CAGR distinguish multi-year growth from a one-year change.
- Database connections close once in a `finally` block; source evidence and amounts commit together.
- The chart-list indentation issue in the supplied main.py is fixed.

## Financial definitions

All amounts are in USD millions.

- Net profit margin = consolidated net income / revenue x 100.
- Total revenue growth = (later revenue / earlier revenue - 1) x 100.
- CAGR = ((later revenue / earlier revenue) ** (1 / year gap) - 1) x 100.
- Margin change is measured in percentage points, not percent.

Total growth is undefined when starting revenue is zero. CAGR requires positive starting revenue and non-negative ending revenue. CAGR uses the difference in fiscal-year labels; fiscal-year lengths can vary slightly. The comparison chart requires positive revenue in both years for all companies; individual analysis can still show losses.

## SEC data selection and audit evidence

Source: https://www.sec.gov/search-filings/edgar-application-programming-interfaces

Each company download retrieves its Company Facts JSON. Selection requires:

1. A standard US-GAAP tag, USD units and a 10-K or 10-K/A filing.
2. An annual accounting period between 350 and 380 days.
3. Revenue and consolidated net income from the SAME filing and exact start/end dates.
4. One unambiguous value per measure. Different supported revenue tags must agree in that selected filing.

The latest matching filing returned for each historical year is selected, so historical values can reflect later revisions or restatements. This is NOT a point-in-time dataset suitable for an unbiased historical trading backtest.

`ProfitLoss` supplies consolidated net income. The program deliberately does not silently substitute shareholder-only `NetIncomeLoss`. Unsupported custom tags or missing consolidated-profit tags can make a year unavailable even if an annual report exists.

These three companies use December fiscal year ends; the year selector groups observations by the calendar year of the period end, not the API's `fy` field (which can identify a later filing year for a comparative observation). Adding companies with other fiscal calendars requires reviewing that convention.

The `financial_sources` table stores the selected filing accession, filing date, accounting period, revenue tag and retrieval timestamp. Source evidence appears in individual company analysis. Look up a filing using its accession on SEC EDGAR. Net income always uses `ProfitLoss`, and stored amounts always use USD millions.

The downloader makes three sequential requests with pauses; it does not continually poll the SEC. Current SEC requests may fail due to connection or access restrictions; the terminal reports the error and leaves financial updates uncommitted.

## Limitations

- Only KO, PEP and KDP are configured; this is not a general company screener.
- Availability depends on supported SEC facts, not just when a company was founded. Current-company histories may include predecessor businesses or acquisitions; do not assume unchanged business scope.
- Business mixes, reporting changes, unusual items and acquisitions affect comparisons. The application calculates differences; it does not explain their causes or establish investment value.
- A failed six-record update leaves existing financial amounts intact. Existing cached data is not automatically refreshed; choose UPDATE.
- Removing a row from the CSV does not delete it from the database. The CSV only seeds missing records.

## Verification

Run offline tests from this folder:

```bash
python -m unittest discover -s tests -v
```

The tests use synthetic SEC-shaped responses. They cover annual/quarterly separation, 53-week years, matching periods and filings, revisions, conflicting facts, growth calculations, selected-year filtering, database rollback, missing years, CSV preservation, and chart generation.

These offline checks do not establish that every historical year is available from the live SEC service. Run UPDATE locally with your contact email to verify a particular pair.

## Learning context

Personal learning project developed with assistance. Read the source comments and functions to understand the API, accounting rules and database workflow before presenting the project in interviews.
