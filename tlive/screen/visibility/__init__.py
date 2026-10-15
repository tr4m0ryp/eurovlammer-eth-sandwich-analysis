"""Visibility books: which buys WE could see in time, and which of those our capital can sandwich.

  LiveVisibility     every interesting buy, tiered by our lag, its router and its tip.
  StrictVisibility   the visible ones whose budget-capped sandwich clears profit and ROI gates.
"""
from .live import LiveVisibility
from .strict import StrictVisibility
from .tier import tier

__all__ = ["LiveVisibility", "StrictVisibility", "tier"]
