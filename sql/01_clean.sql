-- 01_clean.sql
-- Cleans the raw export in small, named steps so the cleaning log can count
-- how many rows each rule removed.
-- Input table : raw_ad_daily   (loaded from data/raw/*.csv by src/build_dataset.py)
-- Output table: ad_daily_clean

-- Step 1: remove exact duplicate rows (export glitches)
DROP TABLE IF EXISTS s1_dedup;
CREATE TABLE s1_dedup AS
SELECT DISTINCT * FROM raw_ad_daily;

-- Step 2: remove days where the ad was not delivered at all
DROP TABLE IF EXISTS s2_delivered;
CREATE TABLE s2_delivered AS
SELECT * FROM s1_dedup
WHERE impressions > 0;

-- Step 3: remove ads with fewer than 3 delivered days (quick tests, not enough
-- history to judge fatigue)
DROP TABLE IF EXISTS s3_min_history;
CREATE TABLE s3_min_history AS
SELECT * FROM s2_delivered
WHERE ad_id IN (
    SELECT ad_id
    FROM s2_delivered
    GROUP BY ad_id
    HAVING COUNT(*) >= 3
);

-- Step 4: recompute every ratio from the raw counts instead of trusting the
-- export, and keep frequency NULL when reach is missing (filled in 02_features)
DROP TABLE IF EXISTS ad_daily_clean;
CREATE TABLE ad_daily_clean AS
SELECT
    date,
    ad_id,
    campaign_id,
    format,
    hook_type,
    launch_date,
    spend_inr,
    impressions,
    reach,
    clicks,
    purchases,
    CASE WHEN reach > 0 THEN ROUND(1.0 * impressions / reach, 3) END AS frequency,
    ROUND(100.0 * clicks / impressions, 4)                            AS ctr_pct,
    ROUND(1000.0 * spend_inr / impressions, 2)                        AS cpm_inr,
    CASE WHEN purchases > 0 THEN ROUND(spend_inr / purchases, 2) END  AS cpa_inr
FROM s3_min_history
ORDER BY ad_id, date;
