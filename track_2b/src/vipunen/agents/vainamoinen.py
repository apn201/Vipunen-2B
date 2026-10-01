"""Vainamoinen - verse stage (Apertus). Masked claim -> 500+ char Kalevala-metre verse.

Pure literary prompt over masked text: it writes about rabbits. S3 adds the verse checks (length, retry on short output).
"""
from vipunen.agents.base import ApertusStage


class Vainamoinen(ApertusStage):
    owner = "vainamoinen"
