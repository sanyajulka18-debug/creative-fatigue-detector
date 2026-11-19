# Memo: stop paying for tired ads

**To:** Performance marketing team  **Re:** When to refresh Meta ad creatives

## Headline

We spent about **₹13.4 lakh more than we needed to** (14% of a ₹95.2 lakh budget) by keeping ads running after they had stopped performing. A simple refresh rule would have caught almost all of them.

## What we found

- **A third of spend (33%) went to ads on days they had already fatigued.** After fatigue, each purchase cost about 2.5× more (₹261 → ₹667).
- **Formats burn out at very different speeds.** Half of UGC and song ads fatigue by **day 12**; street-interview and podcast ads by day 16; drama ads last about **25 days**.
- **The warning signs are visible early.** How far CTR has slipped from its peak and a rising CPM let a simple model flag about 9 in 10 ads 3 days before they fatigue (recall 0.89, ROC-AUC 0.91).

## The rule

> Refresh an ad the morning after its 3-day CTR has stayed **25% below its own best** for 3 days in a row.

## What to do next week

1. Check the **Refresh alerts** page of the dashboard every morning. Today, 13 of the 15 live ads are flagged.
2. Brief new UGC and song creatives about **every 10 days**; drama creatives can run about three weeks.
3. Run the same analysis on a real Ads Manager export to fine-tune the 25% threshold for our account.

*Caveat: these figures come from a simulated dataset built to mirror real Meta ad behaviour. The method is ready; the exact numbers will change with real data.*
