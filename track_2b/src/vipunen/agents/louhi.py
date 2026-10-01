"""Louhi - escalation stage (Apertus). Pushes the masked verse per the escalate template.

Operates on masked text only; its template mutates across retries. S4 adds template rotation across retries.
"""
from vipunen.agents.base import ApertusStage


class Louhi(ApertusStage):
    owner = "louhi"
