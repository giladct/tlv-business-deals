"""
Entry point.

  python main.py          — scrape then launch dashboard
  python main.py scrape   — scrape only
  python main.py dash     — dashboard only
"""

import sys
import asyncio
import subprocess


def run_scraper():
    from scraper import scrape
    print("=== Scraping Skyscanner ===")
    results = asyncio.run(scrape())
    print(f"=== Done: {len(results)} exotic flights saved ===\n")
    return results


def run_dashboard():
    print("=== Launching dashboard (http://localhost:8501) ===")
    subprocess.run(["streamlit", "run", "dashboard.py"], check=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "both"

    if cmd == "scrape":
        run_scraper()
    elif cmd == "dash":
        run_dashboard()
    else:
        run_scraper()
        run_dashboard()
