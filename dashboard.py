import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(
    page_title="TLV Business Class Deals",
    page_icon="✈️",
    layout="wide",
)

st.title("✈️ Cheap Business Class from TLV")
st.caption("Exotic destinations · Deals = 30 % or more below average price")

# ── Load data ────────────────────────────────────────────────────────────────
json_path = Path(__file__).parent / "data" / "flights.json"

if json_path.exists():
    flights = json.loads(json_path.read_text(encoding="utf-8"))
else:
    from storage import init_db, load_latest_flights
    init_db()
    flights = load_latest_flights()

if not flights:
    st.warning("No data yet. Run the scraper first: `python scraper.py`")
    st.stop()

df = pd.DataFrame(flights)
df["price_usd"] = pd.to_numeric(df["price_usd"], errors="coerce")
df = df.dropna(subset=["price_usd"])

avg_price = df["price_usd"].mean()
deal_threshold = avg_price * 0.70
df["is_deal"] = df["price_usd"] <= deal_threshold
df["pct_below_avg"] = ((avg_price - df["price_usd"]) / avg_price * 100).round(1)
if "scraped_at" in df.columns:
    df["scraped_at"] = pd.to_datetime(df["scraped_at"]).dt.strftime("%Y-%m-%d %H:%M")

df["buy_link"] = df["destination"].apply(
    lambda d: "https://www.google.com/travel/flights?hl=en&curr=USD&q=flights+from+tel+aviv+to+"
              + d.lower().replace(" ", "+")
)

# ── Top KPIs ─────────────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
col1.metric("Destinations found", len(df))
col2.metric("Deals (≥30 % off avg)", int(df["is_deal"].sum()))
col3.metric("Average price", f"${avg_price:,.0f}")
cheapest = df.loc[df["price_usd"].idxmin()]
col4.metric("Cheapest", f"${cheapest['price_usd']:,.0f}", cheapest["destination"])

st.divider()

# ── Filters ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Filters")
    deals_only = st.toggle("Deals only (≥30 % off)", value=False)
    max_price = st.slider(
        "Max price (USD)",
        min_value=int(df["price_usd"].min()),
        max_value=int(df["price_usd"].max()),
        value=int(df["price_usd"].max()),
        step=100,
    )
    search = st.text_input("Search destination", "")

filtered = df.copy()
if deals_only:
    filtered = filtered[filtered["is_deal"]]
filtered = filtered[filtered["price_usd"] <= max_price]
if search:
    mask = filtered["destination"].str.contains(search, case=False, na=False)
    filtered = filtered[mask]

# ── Bar chart ─────────────────────────────────────────────────────────────────
st.subheader("Price by Destination")
chart_df = filtered.sort_values("price_usd").head(40)
fig = px.bar(
    chart_df,
    x="destination",
    y="price_usd",
    color="is_deal",
    color_discrete_map={True: "#22c55e", False: "#3b82f6"},
    labels={"price_usd": "Price (USD)", "destination": "Destination", "is_deal": "Deal"},
    text="price_usd",
    height=420,
)
fig.update_traces(texttemplate="$%{text:,.0f}", textposition="outside")
fig.add_hline(
    y=deal_threshold,
    line_dash="dash",
    line_color="red",
    annotation_text=f"Deal threshold ${deal_threshold:,.0f}",
)
fig.update_layout(xaxis_tickangle=-35, showlegend=True)
st.plotly_chart(fig, use_container_width=True)

# ── Table with buy links ───────────────────────────────────────────────────────
st.subheader("All Results")

display_cols = ["destination", "country", "price_usd", "pct_below_avg", "is_deal", "buy_link"]
if "scraped_at" in filtered.columns:
    display_cols.append("scraped_at")
available = [c for c in display_cols if c in filtered.columns]

st.dataframe(
    filtered[available].sort_values("price_usd").rename(columns={
        "destination": "Destination",
        "country": "Country",
        "price_usd": "Price (USD)",
        "pct_below_avg": "% Below Avg",
        "is_deal": "Deal",
        "buy_link": "Book",
        "scraped_at": "Scraped At",
    }),
    column_config={
        "Price (USD)": st.column_config.NumberColumn(format="$%d"),
        "% Below Avg": st.column_config.NumberColumn(format="%.1f%%"),
        "Deal": st.column_config.CheckboxColumn(),
        "Book": st.column_config.LinkColumn("Book", display_text="Buy on Google Flights"),
    },
    use_container_width=True,
    hide_index=True,
    height=500,
)

# ── Price trend over time ─────────────────────────────────────────────────────
st.divider()
st.subheader("Price Trend Over Time")

history_path = Path(__file__).parent / "data" / "history.json"
if history_path.exists():
    history = json.loads(history_path.read_text(encoding="utf-8"))
    hist_df = pd.DataFrame(history)
    hist_df["date"] = pd.to_datetime(hist_df["date"])
    hist_df["price_usd"] = pd.to_numeric(hist_df["price_usd"], errors="coerce")

    num_days = hist_df["date"].nunique()
    if num_days < 2:
        st.info("Trend data will appear here after the second daily scrape. Check back tomorrow!")
    else:
        all_dests = sorted(hist_df["destination"].unique())
        # Default to deal destinations if any, otherwise first 5
        deal_dests = df[df["is_deal"]]["destination"].tolist()
        default_dests = deal_dests if deal_dests else all_dests[:5]
        selected = st.multiselect("Select destinations to compare", all_dests, default=default_dests)

        if selected:
            trend_df = hist_df[hist_df["destination"].isin(selected)].sort_values("date")
            fig2 = px.line(
                trend_df,
                x="date",
                y="price_usd",
                color="destination",
                markers=True,
                labels={"price_usd": "Price (USD)", "date": "Date", "destination": "Destination"},
                height=400,
            )
            fig2.update_traces(mode="lines+markers")
            fig2.update_layout(yaxis_tickprefix="$", yaxis_tickformat=",")
            st.plotly_chart(fig2, use_container_width=True)
else:
    st.info("Trend data will appear here after the first scrape completes.")
