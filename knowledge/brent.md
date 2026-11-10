# Knowledge · Brent

What we have learned about the Planning London Datahub data for the **London Borough
of Brent**, one verified fact at a time. Brent only: Lewisham gets its own file
(`knowledge/lewisham.md`), built the same way once Brent is finished.

## 1. Every Brent application has the same 52 top-level fields

For Brent, the `_source` dict of every application in the `applications` index has
**exactly 52 top-level fields**, whatever its date. Empty fields are still present,
with the value `null` (`None` in Python), so the set of keys never changes.

Verified on 2026-11-09 against all 36,953 Brent applications (download of
2026-11-06), and on two live records (`Brent-22_4070`, `Brent-21_3094`).

The 52 fields:

- **Identity:** `id`, `lpa_app_no`, `lpa_name`, `borough`, `ward`, `bo_system`, `pp_id`
- **Site and address:** `site_name`, `site_number`, `street_name`,
  `secondary_street_name`, `locality`, `postcode`, `uprn`, `subdivision_of_building`,
  `title_number`
- **Location:** `centroid`, `centroid_easting`, `centroid_northing`, `polygon`,
  `wgs84_polygon`
- **What it is:** `description`, `application_type`, `application_type_full`,
  `development_type`
- **Decision:** `valid_date`, `decision_target_date`, `last_date_consultation_comments`,
  `decision`, `decision_date`, `decision_process`, `decision_agency`,
  `decision_conditions`, `status`, `lapsed_date`, `cil_liability`, `epc_number`
- **Appeal:** `appeal_status`, `appeal_start_date`, `appeal_decision`,
  `appeal_decision_date`
- **Building work:** `actual_commencement_date`, `actual_completion_date`,
  `reference_no_of_permission_being_relied_on`,
  `date_building_work_started_under_previous_permission`,
  `date_building_work_completed_under_previous_permission`
- **Nested detail:** `application_details`, `parking_details`
- **Datahub bookkeeping:** `last_updated`, `last_synced`, `last_updated_by`,
  `url_planning_app`

Most are single values. These hold more than one value:

| Field                                     | Holds                                                                                                |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `application_details`                   | a dict with most of the ~276 nested fields, including the lists (residential units, floorspace, ...) |
| `parking_details`                       | a dict of 27 numbers (existing / proposed / net, per vehicle type)                                   |
| `centroid`                              | a dict: `lat`, `lon`                                                                             |
| `polygon`, `wgs84_polygon`            | GeoJSON dicts (site boundary)                                                                        |
| `title_number`, `decision_conditions` | lists                                                                                                |

The index mapping (`GET /applications`) defines **55** top-level fields. The 3 that
never appear in Brent records: `is_pre_app`, `pp_retry_expiry`, `pp_update_required`.

## 2. Field definitions (from the technical schema)

Definitions copied word for word from the `applications` sheet of
`planninglondondatahub_public_technical_schemav2.1.xlsx` (column "Field Definition"),
for the 52 top-level fields in section 1, in the schema's order. Only whitespace was
tidied (line breaks and double spaces). Containers are not defined in the schema
themselves; their sub-fields are.

| Field name                                                 | Definition                                                                                                                      |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `actual_commencement_date`                               | Contains the actual commencement date for the development as a whole (DD/MM/YYYY)                                               |
| `actual_completion_date`                                 | Contains the actual completion date for the development as a whole (DD/MM/YYYY)                                                 |
| `appeal_decision`                                        | Describes the decision of the appeal made by the planning inspectorate, see Field Values tab for all appeal decision types      |
| `appeal_decision_date`                                   | Contains the appeal decision date for the application (DD/MM/YYYY)                                                              |
| `appeal_start_date`                                      | Contains the appeal start date for the application (DD/MM/YYYY)                                                                 |
| `appeal_status`                                          | Contains the appeal status for the application, see Field Values tab for all appeal status types                                |
| `application_type`                                       | Describes the generic Application Type, see Field Values tab for all Application Types                                          |
| `application_type_full`                                  | Contains the type of application form selected by the applicant, see Field Values tab for all Planning Portal Application Types |
| `bo_system`                                              | Contains the name of the back office system used                                                                                |
| `borough`                                                | Describes the name of the Borough                                                                                               |
| `centroid`                                               | Contains the Centroid showing the location of the decision                                                                      |
| `centroid_easting`                                       | Contains Easting for the Centroid showing the location of the decision                                                          |
| `centroid_northing`                                      | Contains Northing for the Centroid showing the location of the decision                                                         |
| `cil_liability`                                          | Describes if the development is liable to CIL                                                                                   |
| `date_building_work_completed_under_previous_permission` | Contains the date that work completed under a previous permission                                                               |
| `date_building_work_started_under_previous_permission`   | Contains the date that work started under a previous permission                                                                 |
| `decision`                                               | Describes the decision given, see Field Values tab for all Decision Types                                                       |
| `decision_agency`                                        | Contains the Decision Agency, see Field Values tab for all Decision Agency Types                                                |
| `decision_conditions`                                    | Describes the conditions associated with the decision                                                                           |
| `decision_date`                                          | Contains the decision date for the application (DD/MM/YYYY)                                                                     |
| `decision_process`                                       | Describes the process by which a decision was made, see Field Values tab for all Decision Process Types                         |
| `decision_target_date`                                   | Contains the decision target date for the application (DD/MM/YYYY)                                                              |
| `description`                                            | Provides a general description of proposed development                                                                          |
| `development_type`                                       | Describes the Development Type, see Field Values for all Development Types                                                      |
| `epc_number`                                             | Describes the most recent Energy Performance Certificate number subject to the property having one                              |
| `id`                                                     | The unique system reference number. This is made up of the LPA Name and the LPA's Application Reference Number.                 |
| `lapsed_date`                                            | Contains the date the permission lapses if building work does not commence (DD/MM/YYYY)                                         |
| `last_date_consultation_comments`                        | Contains the last date for consultation comments to be recevied (DD/MM/YYYY)                                                    |
| `last_synced`                                            | Date of which the last sync with the Public Instance of Kibana was made                                                         |
| `last_updated`                                           | Contains the date the application was last updated in the system                                                                |
| `last_updated_by`                                        | Contains the origin of the last update                                                                                          |
| `locality`                                               | Describes the locality of the site                                                                                              |
| `lpa_app_no`                                             | Describes the reference number assigned by the Local Planning Authority                                                         |
| `lpa_name`                                               | Contains the Local Planning Authority name, see Field Values tab for all available LPAs                                         |
| `polygon`                                                | Contains the polygon associated with the development                                                                            |
| `postcode`                                               | Contains the postcode of the proposed development                                                                               |
| `pp_id`                                                  | Contains the planning portal reference number                                                                                   |
| `reference_no_of_permission_being_relied_on`             | Contains reference number if there has been a permission in principle relating to this development                              |
| `secondary_street_name`                                  | Describes the secondary street name                                                                                             |
| `site_name`                                              | Describes the site name                                                                                                         |
| `site_number`                                            | Describes the site number                                                                                                       |
| `status`                                                 | Describes the current status of the application, see Field Values tab for all Status Types                                      |
| `street_name`                                            | Describes the street name                                                                                                       |
| `subdivision_of_building`                                | Describes details of subdivision of building the permission relates to, null if whole building                                  |
| `title_number`                                           | Describes the Land Registry title number that distinguishes a property from those with similar addresses                        |
| `uprn`                                                   | Contains Unique Property Reference Number for the propose site                                                                  |
| `url_planning_app`                                       | Contains the website address to the published planning application                                                              |
| `valid_date`                                             | Contains the date at which the application is valid (DD/MM/YYYY)                                                                |
| `ward`                                                   | Describes the Ward in which the proposed development resides within.                                                            |
| `wgs84_polygon`                                          | Contains the polygon associated with the development                                                                            |
| `application_details`                                    | *This is a container.*                                                                                                        |
| `parking_details`                                        | *This is a container.*                                                                                                        |

## 3. Applications can be related to each other

One application might be related to another. Some links are on the application
itself, but others are not and have to be found by searching the other applications.

- On the application itself: `application_details.superseding_details` (the
  applications it replaces).
- Only on the other applications: the newer application that replaces it, follow-ups
  (discharge of conditions, amendments) that mention its reference in their
  `description`, and other applications on the same site.

## 4. Field facts live in `semantic/brent/`

Facts about each field (meaning, format, coverage, values, known issues and rules
for the chat bot) are kept as **one YAML file per field** in
`semantic/brent/`, so they can be read by code later. This file keeps the general
learnings only.

All cleaning rules (applied when loading into the database, so the EDA never has to
be repeated) are in one file, `semantic/brent/cleaning.yaml`. The field files describe
the data as it is after cleaning.

| Field | File | Notebook |
|---|---|---|
| *(cleaning)* | `semantic/brent/cleaning.yaml` | — |
| `lpa_app_no` | `semantic/brent/lpa_app_no.yaml` | `findings/brent/lpa_app_no.ipynb` |
| `status` | `semantic/brent/status.yaml` | `findings/brent/status.ipynb` |
| `decision` | `semantic/brent/decision.yaml` | `findings/brent/decision.ipynb` |
| `decision_date` | `semantic/brent/decision_date.yaml` | `findings/brent/decision_date.ipynb` |
| `valid_date` | `semantic/brent/valid_date.yaml` | `findings/brent/valid_date.ipynb` |
| `application_type` | `semantic/brent/application_type.yaml` | `findings/brent/application_type.ipynb` |
| `description` | `semantic/brent/description.yaml` | `findings/brent/description.ipynb` |
| `decision_target_date` | `semantic/brent/decision_target_date.yaml` | `findings/brent/decision_target_date.ipynb` |
| `appeal_decision` | `semantic/brent/appeal_decision.yaml` | `findings/brent/appeal_decision.ipynb` |
| `appeal_decision_date` | `semantic/brent/appeal_decision_date.yaml` | `findings/brent/appeal_decision_date.ipynb` |
| `borough` | `semantic/brent/borough.yaml` | `findings/brent/borough.ipynb` |
| `legacy_record` (from `bo_system`) | `semantic/brent/legacy_record.yaml` | `findings/brent/url_planning_app.ipynb` (Step 4), `valid_date.ipynb` (Step 5) |
| `address` (from `site_name`, `site_number`, `street_name`, `locality`, `postcode`) | `semantic/brent/address.yaml` | `findings/brent/site_name.ipynb`, `site_number.ipynb`, `street_name.ipynb`, `locality.ipynb`, `postcode.ipynb` |

## 5. Groups of records

Some facts are about whole groups of records, not one field.

| Group | How to find it | Size | What's true of it | At load |
|---|---|---|---|---|
| Legacy records | `bo_system` = `FME Legacy Data Import` | 3,531 (3,528 after cleaning) | never have `valid_date`, `decision_target_date` or `url_planning_app` | kept, flagged `legacy_record` |
| Empty records | no `status`, `decision`, `decision_date` or `description` | 407 | also no `borough`, URL or ward; 24 duplicate a full record | dropped (`drop_empty_records`) |
| Test records | ids `Brent-55_0003`, `Brent-55_0015`, `Brent-55_0100` | 3 | test descriptions, `55/` references | dropped (`drop_test_records`) |
| Brent/OPDC duplicates | the same `lpa_app_no` under Brent and OPDC | 4 pairs | same application; the OPDC copy is a legacy or Portal import without dates or URL | OPDC copy dropped (`drop_opdc_duplicates`) |

Rules are in `semantic/brent/cleaning.yaml`; the bot-facing flag is described in
`semantic/brent/legacy_record.yaml`.

## 6. Open questions

Questions we can't answer yet, or that need a decision, are logged in
`knowledge/brent_open_questions.md`.
