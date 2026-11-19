"""
Creative Fatigue Detector - Streamlit dashboard
Run from the project folder:   streamlit run app/dashboard.py
"""
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from lifelines import KaplanMeierFitter
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[1]
NUMERIC = ["days_live", "frequency", "pct_drop_from_peak", "ctr_slope_3d", "cpm_vs_launch"]
CATEGORICAL = ["format", "hook_type"]

st.set_page_config(page_title="Creative Fatigue Detector", layout="wide")


@st.cache_data
def load_data():
    df = pd.read_csv(ROOT / "data/processed/ad_daily_features.csv", parse_dates=["date"])
    summary = pd.read_csv(ROOT / "data/processed/ad_summary.csv")
    df["ctr_slope_3d"] = df["ctr_slope_3d"].fillna(0)
    return df, summary


@st.cache_resource
def train_model(df):
    """Logistic regression on every labelled row (the notebook showed it's as good as a forest)."""
    data = df.dropna(subset=["fatigues_next_3d"])
    prep = ColumnTransformer([("num", StandardScaler(), NUMERIC),
                              ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL)])
    model = Pipeline([("prep", prep),
                      ("model", LogisticRegression(class_weight="balanced", max_iter=1000))])
    model.fit(data[NUMERIC + CATEGORICAL], data["fatigues_next_3d"].astype(int))
    return model


def median_life(summary):
    out = {}
    for fmt, part in summary.groupby("format"):
        kmf = KaplanMeierFitter().fit(part["duration_days"], part["event_observed"])
        out[fmt] = kmf.median_survival_time_
    return pd.Series(out, name="median healthy days").sort_values()


df, summary = load_data()
page = st.sidebar.radio("Page", ["Overview", "Ad explorer", "Format comparison", "Refresh alerts"])
st.sidebar.caption("Data: simulated Meta ad export, Jul-Sep 2026. Fatigue = 3-day CTR at least "
                   "25% below its own peak for 3 days in a row.")

# ------------------------------------------------------------------ Overview
if page == "Overview":
    st.title("Creative Fatigue Detector")
    total = df["spend_inr"].sum()
    fatigued = df.loc[df["is_fatigued"] == 1, "spend_inr"].sum()
    kmf = KaplanMeierFitter().fit(summary["duration_days"], summary["event_observed"])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total spend", f"₹{total / 1e5:.1f} lakh")
    c2.metric("Average CTR", f"{df['clicks'].sum() / df['impressions'].sum() * 100:.2f}%")
    c3.metric("Spend on fatigued days", f"{fatigued / total:.0%}")
    c4.metric("Median healthy life", f"{kmf.median_survival_time_:.0f} days")

    by_format = (df.groupby("format")
                   .agg(ads=("ad_id", "nunique"), spend_inr=("spend_inr", "sum"),
                        median_ctr_pct=("ctr_pct", "median"))
                   .join(median_life(summary))
                   .sort_values("median healthy days"))
    st.subheader("By format")
    st.dataframe(by_format.style.format({"spend_inr": "₹{:,.0f}", "median_ctr_pct": "{:.2f}",
                                         "median healthy days": "{:.0f}"}))

# ------------------------------------------------------------------ Ad explorer
elif page == "Ad explorer":
    st.title("Ad explorer")
    ad_id = st.selectbox("Choose an ad", sorted(df["ad_id"].unique()))
    ad = df[df["ad_id"] == ad_id]
    info = summary.set_index("ad_id").loc[ad_id]
    st.write(f"**Format:** {info['format']} · **Hook:** {info['hook_type']} · "
             f"**Spend:** ₹{info['total_spend_inr']:,.0f}")

    fig = go.Figure()
    fig.add_scatter(x=ad["days_live"], y=ad["ctr_pct"], name="Daily CTR", line=dict(color="lightgrey"))
    fig.add_scatter(x=ad["days_live"], y=ad["ctr_roll3"], name="3-day CTR", line=dict(color="black"))
    fig.add_scatter(x=ad["days_live"], y=ad["peak_ctr_to_date"] * 0.75, name="25% below peak",
                    line=dict(color="red", dash="dot"))
    if pd.notna(info["onset_day"]):
        fig.add_vline(x=info["onset_day"], line_dash="dash", line_color="red",
                      annotation_text=f"fatigued from day {info['onset_day']:.0f}")
    fig.update_layout(xaxis_title="Days since launch", yaxis_title="CTR (%)", height=420)
    st.plotly_chart(fig, width="stretch")

    c1, c2 = st.columns(2)
    c1.plotly_chart(px.line(ad, x="days_live", y="frequency", title="Frequency", height=300),
                    width="stretch")
    c2.plotly_chart(px.line(ad, x="days_live", y="cpm_inr", title="CPM (₹)", height=300),
                    width="stretch")

# ------------------------------------------------------------------ Format comparison
elif page == "Format comparison":
    st.title("Format comparison")
    d = df.copy()
    d["ctr_pct_of_peak"] = d["ctr_roll3"] / d.groupby("ad_id")["ctr_roll3"].transform("max") * 100
    curve = (d[d["days_live"] <= 35].groupby(["format", "days_live"])["ctr_pct_of_peak"]
               .agg(["median", "count"]).reset_index())
    curve = curve[curve["count"] >= 5]
    fig = px.line(curve, x="days_live", y="median", color="format", height=450,
                  labels={"days_live": "Days since launch", "median": "Median CTR (% of own peak)"},
                  title="How fast each format loses its CTR")
    fig.add_hline(y=75, line_dash="dash", line_color="grey", annotation_text="25% below peak")
    st.plotly_chart(fig, width="stretch")
    st.subheader("Median healthy life (Kaplan-Meier)")
    st.bar_chart(median_life(summary))

# ------------------------------------------------------------------ Refresh alerts
else:
    st.title("Refresh alerts")
    st.caption("Ads still running on the last day of data, with the model's risk of fatigue "
               "in the next 3 days.")
    model = train_model(df)
    last_date = df["date"].max()
    live = df[df["date"] == last_date].copy()
    live["risk"] = model.predict_proba(live[NUMERIC + CATEGORICAL])[:, 1]
    # the model only predicts for healthy ads; for ads already fatigued, risk is not meaningful
    live.loc[live["fatigue_detected"] == 1, "risk"] = float("nan")

    def label(row):
        if row["fatigue_detected"] == 1:
            return "Refresh now"
        if row["risk"] >= 0.5:
            return "Watch"
        return "Healthy"

    live["status"] = live.apply(label, axis=1)
    live["order"] = live["status"].map({"Refresh now": 0, "Watch": 1, "Healthy": 2})
    live = live.sort_values(["order", "risk"], ascending=[True, False])
    live["risk"] = live["risk"].map(lambda r: "—" if pd.isna(r) else f"{r:.0%}")
    st.write(f"Data up to **{last_date.date()}** · {len(live)} live ads · "
             f"{(live['status'] == 'Refresh now').sum()} to refresh now")
    st.dataframe(
        live[["ad_id", "format", "days_live", "ctr_roll3", "pct_drop_from_peak", "frequency", "risk", "status"]]
        .rename(columns={"ctr_roll3": "3-day CTR %", "pct_drop_from_peak": "drop from peak"})
        .style.format({"3-day CTR %": "{:.2f}", "drop from peak": "{:.0%}", "frequency": "{:.2f}"}),
        hide_index=True, width="stretch")
