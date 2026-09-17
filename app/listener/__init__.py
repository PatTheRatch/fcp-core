"""The in-season listener: a player's status and ownership as a time series.

Layer 1 of docs/pickups.md. `pool` parses what ESPN serves, `events` turns
two consecutive observations into what changed, and `status` is the pass
that runs three times a day and writes it all down.
"""
