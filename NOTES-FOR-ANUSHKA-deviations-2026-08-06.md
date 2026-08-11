# Deviations-Excel fixes + source follow-ups, Anushka (2026-08-06, commit 26ec8737)

Fixed the Deviations Excel on the live dashboard. **Some of these must also go into your build / the MIS source file, or they revert on the next refresh:**

## Fixed in index.html (fold into your generator)
1. **RM column was blank** — `getAllClientsFlat()` read `parking.rm`/`parking.srm`, but `PARKING_DATA` stores `rm_name`/`srm_name`. Now reads `rm_name`. 26/27 clients now show RM.
2. **New clients never detected** — `parseInceptionDate()` only accepted `DD/MM/YYYY`, but inception is stored `YYYY-MM-DD`, so it always returned null. Now accepts both. New clients now flagged: Owen Roncon (AONDCP0002), Amit Kumar (AONDCP0013), Nilesh Khatri (AONDCP0014).
3. **Shah family deviations dropped from Excel** — the export matches `ipsClient` via `full_name.includes(ndas)`, which fails for the combined "Shah Family" record vs members NDAS0011/12/13. Added `ndas_codes:"NDAS0011,NDAS0012,NDAS0013"` to that IPS record so the match works. Consider a proper ndas→IPS index for family/combined records generally.
4. **Gilt tagged as equity** — Mirae Asset CRISIL IBX Gilt Index (ISIN **INF769K01IX5**) comes through the holdings as `DETAILTYPENAME="Equity Fund"`. It's a gilt/debt fund. Add an ISIN override → Debt Fund / Fixed Income in your classification step (like the P8178 fix). (Its V2-equity is 0 so it didn't inflate eq%, but it displayed under equity.)

## Needs the MIS SOURCE file / your build (I only patched the embedded data)
5. **Review statuses** (applied to embedded MIS_DATA; will revert on refresh unless added to NDPMS MIS.xlsx Last Review Date / Action Items):
   - Siddhant Jain (AONDLV0001) & Rakesh Kumar Jain (AONDLV0002): review **done 30-Jun-2026**.
   - CP Gurnani (NDAS0008): **done ~20-Jul-2026**, action "Approval pending from client / RM".
   - Owen (AONDCP0002): In-Process, pending client/RM approval.
   - Dhiraj Sarna (AONDCP0009): In-Process (Preet/Anushka), pending RM approval.
   - Nilesh Khatri (AONDCP0014): In-Process (Preet/Anushka).
   - Sakshat (AONDCP0004) & Shah family (NDAS0011/12/13): pending confirmation from RM.
6. **Nilesh Khatri (AONDCP0014) is NOT in NDPMS MIS.xlsx at all** → no RM/review data. Please add him.
7. **ST00103** — a *Funded* second account for Sharad Jagtiani exists in the MIS but is not on the dashboard. Confirm whether it should be added or is a duplicate/closed account.

## MIS notes
- No "IPS Yes/No" column in the current NDPMS MIS.xlsx — the passive/no-IPS list is derived elsewhere and may be stale; consider re-adding that column.
- Duplicate rows in MIS: Harish, Jyoti, Sharad each appear twice (second is unfunded/alt account).
