# Changelog of AISC Control Objectives, newest first.

## 2026-10-16: straight into the assessment

- `/p/{project}` (the launcher's Set control objectives card) redirects to the assessment of the project's latest card version, the page with the left menu, or to the assessments page when that version has none, there is no card yet or the platform does not answer. The home page (`home.html.j2`, its styles, `render_home_page`, the assessment count) is gone. Check: `tests/test_projects.py::TestTheProjectRoot`.

## 2026-10-16: review fixes

- `ai.mapping.completed` of a run where some risks failed records, for each failed risk, the rows it kept (the matrix's), not the failed call's empty mapping (`test_ledger` `test_a_risk_whose_model_call_fails_keeps_its_rows`).
- A provider's error is cut to 1200 characters before it is scrubbed, then to 300: a 100 kB error took minutes to scrub inside the run's save (`test_risk_mapping`).
- `Projects._scope`, unused since the 2026-10-14 fix, is gone.

## 2026-10-14: review pass (branch pass/co-2026-10-13)

Areas: project access and project databases, the AI mapping and its errors, by-hand mapping, objective sets, upstream calls, ledger request ids, CORS, tests, README.

Security
- A project reference shaped like a pid is looked up only as a pid, anything else only as a slug, and membership is read on that one project: a project whose slug was another project's pid can no longer open the other project's database (`test_project_access`, `test_isolation_project_databases`).
- A provider's error is stored with credentials and key-like strings taken out and cut to 300 characters, and only editors and owners see it on the page and in `GET /p/{p}/api/projects[/{id}]`; viewers see the stop code only (`test_api_auth`).
- `X-AISC-Request-Id` is cited in ledger events only when the request carries a verifying gateway token (`azp` = `AISC_GATEWAY_CLIENT_ID`, default `aisc-gateway`); with `AUTH_ENABLED=false` the presented id is kept (`test_ledger_request_id`).
- CORS allows no other origin when `CONTROL_OBJECTIVES_CORS_ORIGINS` is unset (was any origin).

Behaviour
- An AI run in which every model call fails saves nothing, records `ai.mapping.failed` and answers 502; in a partial failure the failed risks keep their rows and their objectives stay selected (`test_ledger`).
- An AI run's "unchanged since I read it" check and a person's mapping run under a lock on the assessment row: concurrent mappings both reach the selection, an overwrite answers 409, and a person's mapping after a profile switch answers 409 (`test_assessment_profile`).
- Objective numbers and set version numbers are taken under a lock on the set's row; two sets made at once with one code give the "already a set's" message instead of a 500.
- Rating, keys and mappings move the assessment's `updated_at`, so the projects page orders by the last change (`test_projects`).
- Starting an assessment answers 502, not 500, when the platform or qualification answers 200 with the wrong shape (a sign-in page, other JSON, a card that is not UTF-8) (`test_assessment_of_a_version`).
- Removed three uncalled functions: `ProjectRepository.save_selection`, `ProjectRepository.previous_of`, `Library.profile_version_label`.

Tests and docs
- A test audit: 48 redundant or never-failing tests retired or rewritten; whole suite 521 passed, 1 skipped.
- README and `.env.example` corrected (unrated risks count 0, reader grants, default model, the changes above).

How to check: from the aisc repo's `apps/control-objectives`, `bash ../../scripts/lib/co-tests.sh` (throwaway Postgres; expect 521 passed, 1 skipped) and `uv run --extra dev ruff check src tests`. On a running stack: a viewer's project page shows no AI error text, and with a revoked model key "Suggest with AI" answers 502 and leaves the matrix as it was.
