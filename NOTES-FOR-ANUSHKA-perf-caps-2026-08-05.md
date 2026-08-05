# Perf + market-cap fixes, Anushka (2026-08-05, commit 4f9d2000)

Two data bugs found & fixed on the live dashboard — please fold into your build so they don't come back:

## 1. Client performance was wrong for the oldest / large-inflow clients
Stored returns matched neither the unitized `Client Level NAV Data` nor the MANCOM PDF cumulative ROR (which agree). Sharad Jagtiani since-inception showed **91.2%** (true ~20%), Rajnish **13.4%** (true ~28%), Dhiraj **26%** (true ~2%). Annualized (`si_ann`) also exploded for short-history clients (Owen 425%, Dhiraj 164%) from annualizing sub-year returns.
**Fix:** compute p1m/p3m/p6m/p12m/psi/si_ann from the unitized `Client Level NAV Data` (per-unit NAV is flow-adjusted). si_ann = CAGR only for >=365 days history; sub-year uses raw psi. Guard corrupt/redeemed series (drop NAV<=1.0 — Alka redeemed to 0 on 29-30 Jul).

## 2. LC/MC/SC were not normalized -> false large-cap breaches
Caps were "% of total equity" with international left in the denominator, so intl-heavy clients summed to ~60% and Large Cap read low -> false "<50%" breach (Rajnish LC 37.8, true 61.4; Rakesh 48.7->53.6; Siddhant 42.9->50.0).
**Fix:** normalize LC/MC/SC to 100% of domestic cap-classified equity (intl tracked separately as intl_eq_pct). lc+mc+sc must = 100.

Both are documented in the skill as Step 30. All your other work is untouched; audit clean, no console errors.
