"""
simulate_data.py
----------------
Generates a realistic, *simulated* daily ad-level dataset that looks like a
Meta Ads Manager export. Use this only when you don't have permission to use
a real export. Every other script in this repo works the same way on a real CSV
with the same columns.

How an ad "lives" in this simulation
  1. Learning phase (days 0-2): CTR is slightly below its peak while Meta
     finds the right audience.
  2. Healthy plateau: CTR stays near its peak for a number of days that depends
     on the format (UGC and song ads burn out faster than drama).
  3. Decay: after the break day, CTR falls exponentially as the same people
     keep seeing the ad (frequency rises).
  4. The media buyer pauses the ad some days AFTER it fatigues (reaction lag).
     That lag is the "wasted spend" this project tries to measure.

Messiness added on purpose (so the cleaning step has real work to do):
  duplicate rows, zero-impression days, short-lived test ads, missing reach.

Usage:
    python src/simulate_data.py
Output:
    data/raw/ad_daily_raw.csv
"""

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
N_ADS = 120
START = pd.Timestamp("2026-07-01")
N_DAYS = 90
OUT_PATH = Path(__file__).resolve().parents[1] / "data" / "raw" / "ad_daily_raw.csv"

# Format-level assumptions: (mean peak CTR %, mean healthy days, decay rate per day)
FORMATS = {
    "ugc":              (1.40,  8, 0.090),
    "street_interview": (1.20, 14, 0.060),
    "drama":            (1.00, 18, 0.050),
    "podcast":          (0.90, 12, 0.070),
    "song":             (1.60,  7, 0.100),
}
FORMAT_WEIGHTS = [0.30, 0.25, 0.20, 0.15, 0.10]
HOOK_TYPES = ["question", "shock", "story", "offer"]


def simulate_one_ad(ad_num: int, rng: np.random.Generator) -> list[dict]:
    """Return one row per day for a single ad."""
    fmt = rng.choice(list(FORMATS), p=FORMAT_WEIGHTS)
    peak_mean, healthy_mean, k_mean = FORMATS[fmt]

    ad_id = f"AD_{ad_num:03d}"
    campaign_id = f"CMP_{rng.integers(1, 9):02d}"
    hook = rng.choice(HOOK_TYPES)

    # Each ad gets its own "personality" around the format average
    peak_ctr = peak_mean * rng.lognormal(0, 0.20)
    healthy_days = max(3, int(rng.normal(healthy_mean, healthy_mean * 0.30)))
    if hook == "question":          # small, realistic effect: question hooks last a bit longer
        healthy_days += 2
    k = k_mean * rng.lognormal(0, 0.25)

    base_cpm = rng.uniform(55, 115)          # ₹ per 1,000 impressions
    daily_budget = rng.choice([1500, 2500, 4000, 6000, 8000])
    cvr = rng.uniform(0.015, 0.040)          # click -> purchase rate

    launch_offset = int(rng.integers(0, 70))
    launch_date = START + pd.Timedelta(launch_offset, unit="D")

    # When does the buyer pause it?
    pause_roll = rng.random()
    if pause_roll < 0.12:
        # paused early for unrelated reasons (budget reshuffle) -> censored
        life = int(rng.integers(3, max(4, healthy_days)))
    else:
        # paused some days after fatigue starts; the lag is the waste
        reaction_lag = int(rng.integers(3, 18))
        life = healthy_days + 6 + reaction_lag
    if rng.random() < 0.04:
        life = int(rng.integers(1, 3))       # a quick test ad, killed in 1-2 days

    rows = []
    for day in range(life):
        date = launch_date + pd.Timedelta(day, unit="D")
        if date >= START + pd.Timedelta(N_DAYS, unit="D"):
            break                            # still running when the export was pulled

        # --- CTR curve ---
        if day < 3:
            ctr_shape = 0.85 + 0.05 * day    # learning phase
        elif day < healthy_days:
            ctr_shape = 1.0
        else:
            ctr_shape = np.exp(-k * (day - healthy_days))
        weekend = 0.93 if date.dayofweek >= 5 else 1.0
        ctr = peak_ctr * ctr_shape * weekend * rng.lognormal(0, 0.08)

        # --- Delivery: Meta spends less on ads that stop performing ---
        delivery = min(1.0, ctr_shape ** 0.5) * rng.uniform(0.85, 1.05)
        spend = daily_budget * delivery

        # --- CPM rises as engagement falls ---
        cpm = base_cpm * (1 + 0.6 * (1 - ctr_shape)) * rng.lognormal(0, 0.06)

        impressions = int(spend / cpm * 1000)
        frequency = 1.05 + 0.035 * day + rng.normal(0, 0.04)
        reach = int(impressions / max(frequency, 1.0))
        clicks = int(rng.binomial(impressions, min(ctr / 100, 1)))
        purchases = int(rng.binomial(clicks, cvr))

        rows.append({
            "date": date.date(),
            "ad_id": ad_id,
            "campaign_id": campaign_id,
            "format": fmt,
            "hook_type": hook,
            "launch_date": launch_date.date(),
            "spend_inr": round(spend, 2),
            "impressions": impressions,
            "reach": reach,
            "clicks": clicks,
            "purchases": purchases,
        })
    return rows


def add_messiness(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Add the kind of problems real exports have."""
    df = df.copy()
    # 1. ~1% duplicate rows (export glitches)
    dupes = df.sample(frac=0.01, random_state=SEED)
    # 2. ~0.5% days with zero impressions (ad was in review / rejected that day)
    zero_idx = df.sample(frac=0.005, random_state=SEED + 1).index
    df.loc[zero_idx, ["spend_inr", "impressions", "reach", "clicks", "purchases"]] = 0
    # 3. ~1% missing reach (Ads Manager sometimes leaves it blank)
    na_idx = df.sample(frac=0.01, random_state=SEED + 2).index
    df.loc[na_idx, "reach"] = np.nan
    return pd.concat([df, dupes]).sort_values(["ad_id", "date"]).reset_index(drop=True)


def add_metric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Columns Ads Manager calculates itself."""
    df = df.copy()
    df["frequency"] = (df["impressions"] / df["reach"]).round(2)
    df["ctr_pct"] = (df["clicks"] / df["impressions"] * 100).round(3)
    df["cpm_inr"] = (df["spend_inr"] / df["impressions"] * 1000).round(2)
    df["cpa_inr"] = (df["spend_inr"] / df["purchases"]).round(2)
    df = df.replace([np.inf, -np.inf], np.nan)
    cols = ["date", "ad_id", "campaign_id", "format", "hook_type", "launch_date",
            "spend_inr", "impressions", "reach", "frequency", "clicks", "ctr_pct",
            "cpm_inr", "purchases", "cpa_inr"]
    return df[cols]


def main() -> None:
    rng = np.random.default_rng(SEED)
    rows = []
    for i in range(1, N_ADS + 1):
        rows.extend(simulate_one_ad(i, rng))
    df = pd.DataFrame(rows)
    df = add_messiness(df, rng)
    df = add_metric_columns(df)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)
    print(f"Saved {len(df):,} rows for {df['ad_id'].nunique()} ads -> {OUT_PATH}")


if __name__ == "__main__":
    main()
