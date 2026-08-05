# V2 refresh (31-Jul) — edge cases that need your build, Anushka

**From:** Preet's Claude session · **Date:** 2026-08-05 · **Commit:** `7eafa7a3`

## What I did
Refreshed the V2-dependent metrics against the new `9. V2 Data_31st July_2026.xlsx` (your 31-Jul rebuild used the old 30-Jun V2). To avoid clobbering your work I used a **conservative merge**: applied the genuine V2 deltas where my recompute matched your build (~20 clients, all small ±1-3% moves), and **preserved your values** on the clients below, where my generic recompute diverged from your build's custom handling. `auditLcMcScConsistency` is clean; eq+debt+oth = 100 for all; your code/features are untouched.

## Edge cases I PRESERVED (kept your value) — please refresh these in your own build
Format: client — field — **kept (your build)** vs *my generic recompute*

- **Suman Agarwal** — eq **49.5** / *60.0*, oth **35.1** / *24.6*, LC **74.3** / *62.7*, commodity **32.9** / *22.4*, debt+alt **50.5** / *40.0*.
  Driver: `Ionic Navigate AIF Share Class B1` (**Multi Asset Fund**, not in V2). My rule treats AIF as 100% equity; your build decomposes it into equity/debt/gold. Your handling is correct — mine would overstate equity.
- **Rajnish Kumar** — intl **33.3** / *24.5*, LC **37.8** / *40.9*.
  Driver: partial-overseas exposure on funds not tagged "International Equity". Your build reads V2 Overseas columns (AL/AM); my name-pattern misses some.
- **Harish Agarwal** — LC **17.6** / *0.0*, MC **3.8** / *0.0*. Driver: cap classification your build sources differently for his holdings.
- **Brinda Sankar** — LC **69.6** / *73.9*, MC **27.6** / *23.8*.
- **Amit Kumar** — LC **69.7** / *76.0*.
- **Siddhant Jain** — LC **42.9** / *46.0*.
- Minor (~3-4%, held): Sharad Prem Jagtiani (MC), Rohit Garg (debt/oth), Sankar Subramaniam (MC).

## Ask
Re-run your build pipeline with `9. V2 Data_31st July_2026.xlsx` to refresh the above precisely (for you it's a one-file input swap). **OR** share your 30-Jun V2 file / build script and I can apply just the true V2 deltas without guessing.

Note: in the new V2 file the **ISIN moved to column A** (was column B); eq/debt/others = AH/AI/AJ, caps = AN/AO/AP, AMC = D — unchanged.
