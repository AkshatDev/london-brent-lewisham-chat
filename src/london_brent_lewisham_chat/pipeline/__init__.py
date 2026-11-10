"""Data pipeline: download a borough's applications, clean them, load them into PostGIS.

Layers (PLAN.md §11): dated raw snapshot (JSONL.gz, local only) → ``raw`` schema
(documents as JSONB) → ``core`` schema (typed, cleaned by ``cleaning.yaml``).
"""
