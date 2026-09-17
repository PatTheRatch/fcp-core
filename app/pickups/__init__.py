"""The in-season pickup recommender (docs/pickups.md, layer 2).

Pure functions over stored rows, for one (league season, team): what the
team faces this week (`state`), what a player is worth per game from here
(`projection`), and which swap most improves the week (`stream`). Nothing
here talks to ESPN or prints; the scripts and the API do that.
"""
