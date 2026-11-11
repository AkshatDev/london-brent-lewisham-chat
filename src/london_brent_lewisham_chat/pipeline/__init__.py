"""Data pipeline: download a borough's raw snapshot and clean records.

``extract`` writes a dated raw snapshot (JSONL.gz, local only) for the EDA notebooks.
``clean`` turns one raw record into a cleaned one with the borough's
``cleaning.yaml``; the chat tools use it on every live record they return.
"""
