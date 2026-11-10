# Open questions · Brent

Questions found during the EDA that we can't answer yet, or that need a decision.
Brent only. When one is answered or decided, record the answer in the field's YAML
(`semantic/brent/`) and move the question to "Answered" with the date. Questions put off on purpose go to
"Deferred", with what to do if there is time later.

| # | Question | Context | Found in |
|---|---|---|---|
| 2 | What does `ZZZ` mean? | 16 records, all `Refused`, `appeal_decision` also `ZZZ`, appeal decision date filled. Kept as is for now. Brent's planning portal could confirm (external data, ask first). | `status` |
| 3 | Do we infer a missing `decision` from `status` for the 50 decided records? | `Superseded` 28, `Lapsed` 17, `Allowed` 3, `Dismissed` 2. The 5 Allowed / Dismissed went to appeal, which fits "refused first", but an appeal can also be against non-determination (no decision in time), so not settled. | `decision`, `appeal_decision` |
| 4 | Which is right for the 2 contradictions, `status` or `decision`? | `Brent-25_3290`, `Brent-26_1174`: status Under Consideration, decision Approved, no decision date. | `decision` |
| 5 | What do we do with stale statuses? | `Brent-96_1985` (1996) still says `Appeal In Progress`. **39 more** are still `Application Received` (26) or `Application Under Consideration` (13), with no `decision_date`, and their `decision_target_date` passed over a year ago (checked 2026-11-10); most likely decided or ended long ago but never updated. The 4 Brent/OPDC duplicates (Brent copy kept): for 18/1022, 19/2236, 19/2890 the Brent copy says `Approved`, the dropped OPDC copy `Lapsed`, and the data doesn't say which authority (`lpa_name`) decided. | `decision`, `decision_target_date`, `lpa_app_no` |
| 6 | Is every `55/` reference a test record? | 3 test records found so far, all `55/`; only those 3 ids are dropped. | `decision`, `status` |
| 8 | For the 2 legacy appeal records with the same decision and appeal date, which date is real? | `Brent-DY860529`, `Brent-DY860634` (01/03/1987). | `decision_date` |
| 9 | Do the 11 empty-status records with content get anything more than `Unknown`? | They have a decision date, so they were decided; the outcome isn't recorded. | `status` |
| 13 | Why do the `ZZZ` records have a `lapsed_date`, when a refused application has no permission to lapse? | Always exactly 3 years after the decision. | `status` |

## Deferred

| # | Question | Decision for now | Date | If there is time later |
|---|---|---|---|---|
| 10 | Are the 3 records with `borough = Brent` but a ward from another borough in Brent? **(golden rule)** | Taken as Brent, because `borough` says Brent. | 2026-11-10 | Same location check as #11. Records: `Hammersmith_&_Fulham-2025_01931_FUL`, `Hammersmith_&_Fulham-2025_01998_PMA56` (ward `COLLP`; address 831 Harrow Road, Wembley, NW10 5NH), `OPDC-18_0210_FULOPDC` (ward `East Acton`). Also covers the towns and postcode districts mostly outside Brent in `address.yaml` (`places_outside_brent`). |
| 11 | What is the one record whose `borough` contains "Brent" but isn't exactly `Brent`? **(golden rule)** | Not looked at. Scope stays `borough` = `Brent` (exact match); a record that says Brent is taken as Brent. | 2026-11-10 | Use location (latitude/longitude or postcodes) to find records with `borough` = `Brent` that lie outside Brent's distribution. Context: `match` on `borough` finds 36,546, `term` on `borough.raw` 36,545. |

## Answered

| # | Question | Answer | Date | Recorded in |
|---|---|---|---|---|
| 12 | Which copy do we keep for the 4 applications recorded under both Brent and OPDC? | Keep the Brent copy, drop the OPDC copy (`OPDC-18_1022`, `OPDC-18_2581`, `OPDC-19_2236`, `OPDC-19_2890`). Same application; the Brent copy has valid date, target date and URL and is updated with the regular feed. The status conflict is logged in #5. | 2026-11-10 | `semantic/brent/cleaning.yaml` (`drop_opdc_duplicates`) |
| 1 | What do we do with the 383 empty records that have no status, no decision and no decision date, and no duplicate? | Drop all 407 empty records at load (the 24 duplicates included). They have no status, decision, decision_date or description; the condition matches exactly 407. | 2026-11-09 | `semantic/brent/cleaning.yaml` (`drop_empty_records`) |
| 7 | Is there a larger group of legacy records with no `valid_date`? | Yes: all 3,531 `FME Legacy Data Import` records have no `valid_date` (none has one). The other 397 of the 3,928 are 322 empty records (dropped at load) and 75 real gaps. | 2026-11-09 | `semantic/brent/valid_date.yaml` |
