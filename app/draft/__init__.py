"""Draft framework.

Built bottom up. Valuation turns projections into comparable numbers, the
market model turns those into expected prices, targets say what actually
wins a category in this league, and the optimizer and live room build on
all three.

Every piece is a pure function over data already ingested, so each can be
checked against a season that has already happened rather than trusted.
"""
