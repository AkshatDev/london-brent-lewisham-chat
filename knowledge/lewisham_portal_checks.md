# Lewisham · portal checks for a person

Records the Datahub cannot settle, checked by a person on Lewisham's planning portal
(`planning.lewisham.gov.uk`). This service never reads the portal itself: links go to a
person, who opens them in a browser and pastes back what the page says. Found in
`findings/lewisham/status.ipynb` (Steps 3 and 4), `findings/lewisham/decision.ipynb` (Step 7) and
`findings/lewisham/valid_date.ipynb` (Step 4) and `findings/lewisham/url_planning_app.ipynb` (Step 3).
Data from the live API, 2026-11-11.

## No status, decision date but no decision (5)

| id | Datahub | portal says (2026-11-11) |
|---|---|---|
| [Lewisham-DC_21_120320](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_108241) | no status, no decision; decision_date 03/01/2023 | Status Decided; Decision Granted Deemed Consent; Decision Issued Date 03/01/2023 |
| [Lewisham-DC_21_122223](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_110145) | no status, no decision; decision_date 22/06/2021 | Status Unknown; Decision Not Available; Decision Issued Date 22/06/2021 |
| [Lewisham-DC_22_129646](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_117568) | no status, no decision; decision_date 15/12/2022 | Status Decided; Decision Granted Deemed Consent; Decision Issued Date 15/12/2022 |
| [Lewisham-DC_26_142577](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_130500) | no status, no decision; decision_date 31/03/2026 | Page not available: "removed or restricted from public viewing" |
| [Lewisham-DC_26_143311](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_131234) | no status, no decision; decision_date 15/04/2026, valid_date 20/04/2026 | Status Unknown; Decision Not Available; Decision Issued Date 15/04/2026; Validated 20/04/2026 |

## No status, only a valid date (5)

| id | Datahub | portal says (2026-11-11) |
|---|---|---|
| [Lewisham-DC_24_134407](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_122331) | valid_date 08/01/2024 only | Page not available: "removed or restricted from public viewing" |
| [Lewisham-DC_24_134442](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_122366) | valid_date 12/01/2024 only | Page not available: "removed or restricted from public viewing" |
| [Lewisham-DC_24_134455](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_122379) | valid_date 12/01/2024 only | Page not available: "removed or restricted from public viewing" |
| [Lewisham-DC_24_137303](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_125226) | valid_date 11/09/2024 only | Page not available: "removed or restricted from public viewing" |
| [Lewisham-DC_24_138293](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_126216) | valid_date 21/05/2025 only | Status Unknown; no decision; Received 27/11/2024; Validated 21/05/2025; address Blackheath Business Estate, Blackheath Hill, SE10 8BA |

## Status `Historical`, not a schema value (1)

Found in `findings/lewisham/status.ipynb` (Step 4).

| id | Datahub | portal says (2026-11-11) |
|---|---|---|
| [Lewisham-95_038596](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_22070) | status Historical; decision Refused; decision_date 30/10/1996; valid_date 28/11/2023 | Status Decided; Decision "Created during migration"; Decision Issued Date 30/10/1996; Received Not Available; Validated 28/11/2023 |

## `PRE/` records: what kind of case are they? (4)

Asked in `findings/lewisham/decision.ipynb` (Step 7): `PRE/` records have opinion-type
decisions (Comment Issued, Objection Raised / No Objection to Proposal (OBS only)) and
some descriptions say "Concept meeting". The portal was checked for the case type.

| id | Datahub | portal says (2026-11-11) |
|---|---|---|
| [Lewisham-PRE_19_113483](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_101399) | status Closed; decision Comment Issued; "Concept Meeting: Demolition of two garages…" | Page not available: "removed or restricted from public viewing" |
| [Lewisham-PRE_25_141398](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_129321) | status and decision Objection Raised to Proposal (OBS only) | Page not available: "removed or restricted from public viewing" |
| [Lewisham-PRE_25_140141](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_128064) | status and decision No Objection to Proposal (OBS only) | Page not available: "removed or restricted from public viewing" |
| [Lewisham-PRE_25_139760](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_127683) | status and decision No Objection to Proposal (OBS only) | Page not available: "removed or restricted from public viewing" |

## valid_date years after decision_date: is `/X` a test marker? (1)

Found in `findings/lewisham/valid_date.ipynb` (Step 4): 15 records have valid_date after
decision_date; this one is 12 years apart and its reference ends in `/X`, as do two TEST
records.

| id | Datahub | portal says (2026-11-11) |
|---|---|---|
| [Lewisham-DC_13_082322_X](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_68786) | status and decision Approved; decision_date 10/04/2013; valid_date 11/12/2025; canopy at Blackheath High School, Wemyss Road SE3 | Reference DC/13/082322/X; Status Decided; Decision Granted; Decision Issued Date 10/04/2013; Received 15/01/2013; Validated 11/12/2025 |

## Two records sharing one portal page (2)

Found in `findings/lewisham/url_planning_app.ipynb` (Step 3): 79 pairs of records share a
portal URL (the same application recorded twice). In 77 the current copy was clear; these
two were not.

| id | Datahub | portal says (2026-11-11) |
|---|---|---|
| [Lewisham-DC_20_116497 / Lewisham-20_116497](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_104416) | DC/20/116497 (Portal, updated 2026-01-20): Withdrawn, 10/06/2020; 20/116497 (updated 2026-11-10): Approved, 10/06/2020; Approval of Condition 22 (Energy Saving), Plot 2 of DC/15/92295 | Reference 20/116497; Alternative Reference DE/153/U/TP; Received and Validated 17/04/2020; land bounded by Oxestalls Road, Evelyn Street, Dragoon Road and Grove Street SE8; Status Decided; Decision Granted; Decision Issued Date 10/06/2020 |
| [Lewisham-DC_18_022222](https://planning.lewisham.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_LEWIS_DCAPR_77371) | Application Under Consideration, no decision; valid 18/07/2023; Gaolhouse Court, 114-116 Amersham Vale; description is template text ("[(existing/proposed)]"); the same URL is on the test record TEST/18/022222 | Page not available: "removed or restricted from public viewing" |

## What follows (agreed 2026-11-11)

- **Resolved (2):** DC/21/120320, DC/22/129646: status Decided, decision Granted Deemed
  Consent, from the portal. Not a schema value; loaded as Approved (`outcome_from_portal`).
- **Portal has the same gap (3):** DC/21/122223, DC/26/143311, DC/24/138293. Status
  Unknown; the bot cites this check and says neither the Datahub nor Lewisham's portal
  records an outcome.
- **Page removed or restricted (6):** DC/26/142577, DC/24/134407, DC/24/134442,
  DC/24/134455, DC/24/137303, and DC/18/022222 (added after the shared-page check;
  possibly a test record, not confirmed). Dropped at load (`drop_portal_removed`). Asked about by id,
  the bot says: "This record is excluded: Lewisham's planning portal shows it as removed
  or restricted from public viewing (checked 11 November 2026), and the Datahub has no
  outcome for it." Answering an id lookup with this text instead of a 404 is for the API
  agent (handover).
- **Historical (1):** Lewisham-95_038596 is kept as the Datahub publishes it (status
  Historical, decision Refused); no cleaning rule. The bot gets its context from a known
  issue in `semantic/lewisham/status.yaml`. The 2023 valid_date is on the portal too and
  is likely the migration date (not confirmed); whether other migrated records have one
  is a question for the valid_date EDA.
- **`PRE/` records (4 checked):** all restricted on the portal (2019 and 2025), so the
  portal does not say what kind of case they are. Not confirmed: that they are
  pre-application advice, or what "OBS" means (observations?). Unlike the removed DC/
  records, they are not dropped: the Datahub has their outcome. Described by what the
  data shows; how the bot counts them is still open.
- **DC/13/082322/X (1):** a real application (decided 2013, Granted), not a test; `/X`
  is not a test marker, and what it means is not known. The 2025 validated date is on the
  portal too, so it comes from Lewisham's own system (reset later? not confirmed). Kept,
  no cleaning rule; the bot gives no decision time where valid_date is after
  decision_date.
- **DC/20/116497 / 20/116497:** the portal's reference is 20/116497, Granted; the
  Withdrawn copy DC/20/116497 is dropped as a stale duplicate (`drop_stale_duplicates`),
  the copy updated 2026-11-10 is kept, as for the other 77 pairs.

## Notes, not rules

- From their descriptions, three of the removed records are observations on sites in
  other boroughs: DC/24/134407 (Tower Hamlets), DC/26/142577 (Bromley), DC/24/137303
  (Aylesham Centre; the viewing places named suggest Southwark, not confirmed). How to
  treat records with borough Lewisham and a site elsewhere is still open.
- DC/22/129646: the portal address is 2A Burnt Ash **Hill**; the description says Burnt
  Ash Road.
- DC/24/138293 is in SE10 (Blackheath Hill, on the Lewisham–Greenwich edge); location not
  checked.
