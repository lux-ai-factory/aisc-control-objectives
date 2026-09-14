# Contributing to AISC

Thank you for your interest in contributing to the AISC project!

We welcome contributions from the community, including bug reports, documentation improvements, feature suggestions, and code contributions.

## Contributor License Agreement (CLA)

By submitting a contribution, you confirm that:

- You are the original author of your contribution, or you have the right to submit it.
- You license your contribution under the terms of the [Apache License 2.0](LICENSE.md).
- You agree to the Contributor License Agreement (CLA).

You do not need to sign the CLA separately — by submitting a pull request, issue, or other form of contribution, you implicitly agree to the terms.

## How to Contribute

1. Fork the repository and create your branch from `main`.
2. Follow our coding guidelines and documentation standards.
3. Include appropriate tests and documentation with your pull request.
4. Submit a pull request with a clear description of your changes.

We may request changes or ask questions before merging your contribution.
We reserve the right to reject a pull request for any reason.

## Maintainers

The AISC project is co-developed and co-maintained by the **Université du Luxembourg** and the **Luxembourg Institute of Science and Technology (LIST)**, within the **Interdisciplinary Centre for Security, Reliability and Trust (SnT)** and the **SerVal Research Group**. This initiative is funded under the [Luxembourg AI Factory](https://aifactory.lu/) (Horizon Europe grant agreement n° 101234366).

## Repository-specific notes

- **Test-first.** Every change to the domain (the catalogue, the mapping, the
  tiering, the repository) starts with a failing test. Run the suite before any
  PR: `uv pip install -e '.[dev]' && pytest`. It needs a real PostgreSQL, not
  SQLite (see README, Testing).
- **The model's instructions are markdown**, in `src/aisc_control_objectives/skills/`. Change how
  the mapping reasons there rather than in Python, and pin the new behaviour with
  a test that uses a fake completer.
- **Schema changes need a migration.** Edit `src/aisc_control_objectives/db/tables.py`, then
  `alembic revision --autogenerate`, and check the generated file before
  committing it.
- **Nothing derived gets stored.** Tiers, scores and counts are recomputed on
  every read; a PR that writes them into a table will be asked to explain why.

Thank you for contributing to the AISC project!
