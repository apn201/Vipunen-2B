"""Louhi - escalation stage (Apertus). Pushes the masked verse per the escalate template.

Operates on masked text only; its template mutates across retries. Live call lands in S4.
"""
from vipunen.agents.base import StageAgent


class Louhi(StageAgent):
    owner = "louhi"
