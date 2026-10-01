"""Deterministic per-category scorers: 1/5 pass+fail regex, 2 term counts, 3 PII patterns, 4 n-gram overlap. No LLM in the headline verdict.

Pattern from Virta scoring.py.

Slice: S3 (cat 1, 5), S5 (cat 2, 3, 4). Not implemented yet.
"""
