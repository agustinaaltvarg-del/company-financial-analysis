"""Select two years, download missing SEC data, analyse and chart results."""

import sqlite3
import matplotlib.pyplot as plt

from fetch_data import (
    BASE_DIR, DATABASE_PATH, COMPANIES, choose_years,
    initialise_database, update_years,
)


def has_complete_data(connection, years):
    for ticker in COMPANIES:
        count = connection.execute("""
            SELECT COUNT(*) FROM annual_financials AS f
            JOIN companies AS c ON c.id = f.company_id
            WHERE c.ticker = ? AND f.year IN (?, ?)
        """, (ticker, *years)).fetchone()[0]
        if count != 2:
            return False
    return True


def growth_rates(first_revenue, last_revenue, year_gap):
    total = None if first_revenue == 0 else (
        last_revenue / first_revenue - 1
    ) * 100
    cagr = None
    if first_revenue > 0 and last_revenue >= 0 and year_gap > 0:
        cagr = ((last_revenue / first_revenue) ** (1 / year_gap) - 1) * 100
    return total, cagr


def analyse_company(cursor, ticker, years):
    cursor.execute("""
        SELECT c.name, f.year, f.revenue, f.net_income,
               s.period_start, s.period_end, s.accession, s.filed
        FROM annual_financials AS f
        JOIN companies AS c ON c.id = f.company_id
        LEFT JOIN financial_sources AS s
            ON s.company_id = f.company_id AND s.year = f.year
        WHERE c.ticker = ? AND f.year IN (?, ?)
        ORDER BY f.year
    """, (ticker, *years))
    records = cursor.fetchall()
    if len(records) != 2:
        print(f"Both selected years are needed for {ticker}. Use YEARS or UPDATE.")
        return
    print(f"\n{records[0][0]} | USD millions | Consolidated net income")
    for name, year, revenue, income, start, end, accession, filed in records:
        margin = "N/A" if revenue == 0 else f"{income / revenue * 100:.2f}%"
        print(f"\n{year}: revenue ${revenue:,.2f}m | net income ${income:,.2f}m")
        print(f"Net profit margin: {margin}")
        if accession:
            print(f"Period: {start} to {end} | filing: {accession} ({filed})")
        else:
            print("Source: existing local/CSV record; use UPDATE to verify with SEC.")
    first, last = records
    gap = years[1] - years[0]
    total, cagr = growth_rates(first[2], last[2], gap)
    total_text = "N/A (starting revenue is zero)" if total is None else f"{total:.2f}%"
    print(f"\nTotal revenue growth, {years[0]} to {years[1]}: {total_text}")
    if gap == 1:
        print("This is a one-year change.")
    else:
        print(f"This covers {gap} years, not a one-year change.")
    if cagr is None:
        print("Revenue CAGR: N/A (requires positive starting and non-negative ending revenue)")
    else:
        print(f"Revenue CAGR: {cagr:.2f}% per year (using fiscal-year labels)")
    if first[2] != 0 and last[2] != 0:
        change = (last[3] / last[2] - first[3] / first[2]) * 100
        print(f"Net margin change: {change:+.2f} percentage points")


def compare_companies(cursor, years, show=True):
    data = {}
    for ticker in COMPANIES:
        rows = cursor.execute("""
            SELECT year, revenue, net_income FROM annual_financials AS f
            JOIN companies AS c ON c.id = f.company_id
            WHERE c.ticker = ? AND year IN (?, ?) ORDER BY year
        """, (ticker, *years)).fetchall()
        if len(rows) != 2:
            print(f"Missing selected-year data for {ticker}; comparison cancelled.")
            return
        if any(revenue <= 0 for year, revenue, income in rows):
            print(f"Comparison requires positive revenue in both years for {ticker}.")
            return
        data[ticker] = {year: income / revenue * 100 for year, revenue, income in rows}
    tickers = sorted(data, key=lambda ticker: data[ticker][years[1]], reverse=True)
    print("\nConsolidated net profit margins (ranked by the later year)")
    print(f"{'Company':<24} {years[0]:>10} {years[1]:>10}")
    for ticker in tickers:
        print(f"{COMPANIES[ticker]['name']:<24} "
              f"{data[ticker][years[0]]:>9.2f}% {data[ticker][years[1]]:>9.2f}%")

    fig, ax = plt.subplots(figsize=(10, 5.5))
    colors = ("#8447FF", "#EB25E1")
    for index, year in enumerate(years):
        positions = [position + (index - 0.5) * 0.36 for position in range(len(tickers))]
        bars = ax.bar(positions, [data[ticker][year] for ticker in tickers],
                      width=0.36, label=str(year), color=colors[index])
        ax.bar_label(bars, fmt="%.2f%%", padding=3, fontsize=9)
    ax.set_xticks(range(len(tickers)), [COMPANIES[ticker]["name"] for ticker in tickers])
    ax.set_title(f"Net Profit Margin Comparison: {years[0]} vs {years[1]}")
    ax.set_ylabel("Consolidated net profit margin (%)")
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.legend(title="Fiscal year")
    ax.margins(y=0.2)
    fig.tight_layout()
    filename = BASE_DIR / f"profit_margins_{years[0]}_{years[1]}.png"
    fig.savefig(filename, dpi=200)
    print(f"Chart saved: {filename.name}")
    if show:
        plt.show()
    plt.close(fig)
    return filename


def main():
    connection = sqlite3.connect(DATABASE_PATH)
    email = None
    years = None
    try:
        initialise_database(connection, BASE_DIR / "financial_data.csv")
        cursor = connection.cursor()
        while True:
            if years is None:
                requested = choose_years()
                if requested is None:
                    break
                if not has_complete_data(connection, requested):
                    print("Some records are missing. Downloading all companies for this pair.")
                    if email is None:
                        email = input("Contact email sent to the SEC: ").strip()
                    if not email or "@" not in email:
                        print("Enter a valid contact email.")
                        email = None
                        continue
                    try:
                        update_years(connection, requested, email)
                    except Exception as error:
                        print(f"Could not load that pair: {error}")
                        continue
                years = requested
                print("Using stored records. UPDATE refreshes them from SEC filings.")

            print(f"\n--- Company Financial Analysis: {years[0]} vs {years[1]} ---")
            print("KO / PEP / KDP: analyse | ALL: chart both years")
            print("YEARS: choose another pair | UPDATE: refresh pair | Q: quit")
            choice = input("Your choice: ").strip().upper()
            if choice == "Q":
                break
            elif choice == "YEARS":
                years = None
            elif choice == "ALL":
                compare_companies(cursor, years)
            elif choice == "UPDATE":
                if email is None:
                    email = input("Contact email sent to the SEC: ").strip()
                if not email or "@" not in email:
                    print("Enter a valid contact email.")
                    email = None
                    continue
                try:
                    update_years(connection, years, email)
                except Exception as error:
                    print(f"Update failed: {error}")
            elif choice in COMPANIES:
                analyse_company(cursor, choice, years)
            else:
                print("Choose KO, PEP, KDP, ALL, YEARS, UPDATE or Q.")
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye!")
    except Exception as error:
        print(f"Application error: {error}")
