"""Vainamoinen - verse stage (Apertus). Masked claim -> 500+ char Kalevala-metre verse.

Pure literary prompt over masked text: it writes about rabbits. Live call lands in S3.
"""
from vipunen.agents.base import StageAgent


class Vainamoinen(StageAgent):
    owner = "vainamoinen"
