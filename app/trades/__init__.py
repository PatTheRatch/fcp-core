"""Forward-looking trade evaluation, in the recommender's own currency.

`app.scoring.trade_grades` says what a trade did. This says what one would do,
before it is made, in the same expected-category-wins the pickup recommender
judges an add in -- so "trade for X" and "pick up Y" are two numbers a manager
can read side by side. See docs/trades.md.
"""

from app.trades.calibration import CALIBRATION_NOTE, PUBLISHED, Measured
from app.trades.evaluate import (
    THIN_GAMES,
    TRADE_HURDLE,
    TRADE_REVIEW_DAYS,
    TRADE_REVIEW_SOURCE,
    CategoryView,
    PlayerCard,
    PlayoffLens,
    SideReport,
    TeamOffer,
    TradeReport,
    evaluate_trade,
    playoff_window,
)
from app.trades.summary import summarise

__all__ = [
    "CALIBRATION_NOTE",
    "PUBLISHED",
    "THIN_GAMES",
    "TRADE_HURDLE",
    "TRADE_REVIEW_DAYS",
    "TRADE_REVIEW_SOURCE",
    "CategoryView",
    "Measured",
    "PlayerCard",
    "PlayoffLens",
    "SideReport",
    "TeamOffer",
    "TradeReport",
    "evaluate_trade",
    "playoff_window",
    "summarise",
]
