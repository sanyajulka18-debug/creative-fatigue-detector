-- 02_features.sql
-- Builds the analysis-ready tables from ad_daily_clean using window functions.
--
-- Fatigue definition used in this project:
--   An ad is FATIGUED when its 3-day rolling CTR is at least 25% below its own
--   best rolling CTR so far, for 3 delivered days in a row.
--   * onset day   = the first day of that 3-day streak
--   * detect day  = the third day (the earliest a buyer could know for sure)
-- The 25% threshold is an assumption; notebook 04 tests 15% / 25% / 35%.
--
-- Output tables:
--   ad_daily_features : one row per ad per day, with features + targets
--   ad_summary        : one row per ad (used for survival analysis and savings)

DROP TABLE IF EXISTS ad_daily_features;
CREATE TABLE ad_daily_features AS
WITH base AS (
    SELECT
        c.*,
        CAST(julianday(date) - julianday(launch_date) AS INTEGER)            AS days_live,
        ROW_NUMBER() OVER (PARTITION BY ad_id ORDER BY date)                 AS rn,
        COUNT(*)     OVER (PARTITION BY ad_id)                               AS n_rows,
        -- reach is sometimes blank: borrow frequency from the day before/after
        COALESCE(frequency,
                 LAG(frequency)  OVER (PARTITION BY ad_id ORDER BY date),
                 LEAD(frequency) OVER (PARTITION BY ad_id ORDER BY date))    AS frequency_filled
    FROM ad_daily_clean c
),
launch_cpm AS (
    -- each ad's "normal" CPM = average of its first 3 delivered days
    SELECT ad_id, AVG(cpm_inr) AS launch_cpm
    FROM base
    WHERE rn <= 3
    GROUP BY ad_id
),
rolling AS (
    SELECT
        b.*,
        l.launch_cpm,
        AVG(ctr_pct) OVER (PARTITION BY b.ad_id ORDER BY date
                           ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)         AS ctr_roll3,
        SUM(impressions) OVER (PARTITION BY b.ad_id ORDER BY date
                               ROWS UNBOUNDED PRECEDING)                     AS cum_impressions,
        SUM(spend_inr) OVER (PARTITION BY b.ad_id ORDER BY date
                             ROWS UNBOUNDED PRECEDING)                       AS cum_spend_inr
    FROM base b
    JOIN launch_cpm l USING (ad_id)
),
peaks AS (
    SELECT
        r.*,
        MAX(ctr_roll3) OVER (PARTITION BY ad_id ORDER BY date
                             ROWS UNBOUNDED PRECEDING)                       AS peak_ctr_to_date,
        ctr_roll3 - LAG(ctr_roll3, 3) OVER (PARTITION BY ad_id ORDER BY date) AS ctr_slope_3d,
        cpm_inr / launch_cpm - 1                                             AS cpm_vs_launch
    FROM rolling r
),
drops AS (
    SELECT
        p.*,
        1 - ctr_roll3 / peak_ctr_to_date                                      AS pct_drop_from_peak,
        CASE WHEN days_live >= 3 AND 1 - ctr_roll3 / peak_ctr_to_date >= 0.25
             THEN 1 ELSE 0 END                                               AS is_drop_day
    FROM peaks p
),
streaks AS (
    SELECT
        d.*,
        SUM(is_drop_day) OVER (PARTITION BY ad_id ORDER BY date
                               ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)     AS drop_days_last3
    FROM drops d
),
onset AS (
    -- first row where 3 drop days in a row are completed = detection row
    SELECT ad_id, MIN(rn) AS detect_rn, MIN(rn) - 2 AS onset_rn
    FROM streaks
    WHERE drop_days_last3 = 3
    GROUP BY ad_id
)
SELECT
    s.date, s.ad_id, s.campaign_id, s.format, s.hook_type, s.launch_date,
    s.spend_inr, s.impressions, s.reach, s.clicks, s.purchases,
    s.frequency_filled                       AS frequency,
    s.ctr_pct, s.cpm_inr, s.cpa_inr,
    s.days_live, s.rn,
    ROUND(s.ctr_roll3, 4)                    AS ctr_roll3,
    ROUND(s.peak_ctr_to_date, 4)             AS peak_ctr_to_date,
    ROUND(s.pct_drop_from_peak, 4)           AS pct_drop_from_peak,
    ROUND(s.ctr_slope_3d, 4)                 AS ctr_slope_3d,
    ROUND(s.cpm_vs_launch, 4)                AS cpm_vs_launch,
    s.cum_impressions, ROUND(s.cum_spend_inr, 2) AS cum_spend_inr,
    s.is_drop_day,
    CASE WHEN o.onset_rn IS NOT NULL AND s.rn >= o.onset_rn  THEN 1 ELSE 0 END AS is_fatigued,
    CASE WHEN o.detect_rn IS NOT NULL AND s.rn >= o.detect_rn THEN 1 ELSE 0 END AS fatigue_detected,
    -- MODEL TARGET: will this (still healthy) ad fatigue within the next 3 days?
    --   NULL = row cannot be used: ad already fatigued, or the ad was still
    --   running at export time and we can't see its next 3 days (censored).
    CASE
        WHEN o.onset_rn IS NOT NULL AND s.rn >= o.onset_rn             THEN NULL
        WHEN o.onset_rn IS NOT NULL AND o.onset_rn - s.rn <= 3          THEN 1
        WHEN o.onset_rn IS NOT NULL                                     THEN 0
        WHEN s.rn > s.n_rows - 3                                        THEN NULL
        ELSE 0
    END                                                                 AS fatigues_next_3d
FROM streaks s
LEFT JOIN onset o USING (ad_id)
ORDER BY s.ad_id, s.date;


DROP TABLE IF EXISTS ad_summary;
CREATE TABLE ad_summary AS
WITH per_ad AS (
    SELECT
        ad_id, campaign_id, format, hook_type, launch_date,
        MIN(date)                                   AS first_date,
        MAX(date)                                   AS last_date,
        COUNT(*)                                    AS days_delivered,
        MAX(days_live)                              AS last_days_live,
        MAX(ctr_roll3)                              AS peak_ctr_roll3,
        SUM(spend_inr)                              AS total_spend_inr,
        SUM(purchases)                              AS total_purchases,
        MAX(is_fatigued)                            AS fatigued,
        MIN(CASE WHEN is_fatigued = 1 THEN days_live END)      AS onset_day,
        MIN(CASE WHEN fatigue_detected = 1 THEN days_live END) AS detect_day,
        SUM(CASE WHEN is_fatigued = 1 THEN spend_inr ELSE 0 END)      AS spend_after_onset_inr,
        SUM(CASE WHEN fatigue_detected = 1 THEN spend_inr ELSE 0 END) AS spend_after_detect_inr,
        SUM(CASE WHEN is_fatigued = 1 THEN purchases ELSE 0 END)      AS purchases_after_onset
    FROM ad_daily_features
    GROUP BY ad_id, campaign_id, format, hook_type, launch_date
)
SELECT
    p.*,
    -- survival analysis inputs: time = days until fatigue (or until we stop
    -- watching), event = 1 if fatigue was observed, 0 if censored
    CASE WHEN fatigued = 1 THEN onset_day ELSE last_days_live END AS duration_days,
    fatigued                                                      AS event_observed,
    CASE WHEN last_date = (SELECT MAX(date) FROM ad_daily_clean) THEN 1 ELSE 0 END AS running_at_export
FROM per_ad p
ORDER BY ad_id;
