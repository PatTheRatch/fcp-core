"""teams matchup periods rosters

Teams and their owners, the season's matchup periods and the matchups inside
them, and a roster snapshot per team per period.

Owners and players are global tables, keyed on the identifiers ESPN keeps
stable across seasons. Teams are season-scoped, like `league_seasons`,
because a team can be renamed or change hands between years. Rosters are
pinned to a matchup period rather than stored as a "current roster", since a
roster changes with every waiver claim.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "owners",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("espn_owner_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("first_name", sa.String(), nullable=True),
        sa.Column("last_name", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("espn_owner_id"),
    )
    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("espn_player_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("espn_player_id"),
    )
    op.create_table(
        "matchup_periods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_season_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.Integer(), nullable=False),
        sa.Column("is_playoff", sa.Boolean(), nullable=False),
        sa.Column("final_scoring_period", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["league_season_id"], ["league_seasons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("league_season_id", "period", name="uq_matchup_periods_season_period"),
    )
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_season_id", sa.Integer(), nullable=False),
        sa.Column("espn_team_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("abbreviation", sa.String(), nullable=True),
        sa.Column("logo_url", sa.String(), nullable=True),
        sa.Column("division_id", sa.Integer(), nullable=True),
        sa.Column("division_name", sa.String(), nullable=True),
        sa.Column("standing", sa.Integer(), nullable=True),
        sa.Column("final_standing", sa.Integer(), nullable=True),
        sa.Column("categories_won", sa.Integer(), nullable=False),
        sa.Column("categories_lost", sa.Integer(), nullable=False),
        sa.Column("categories_tied", sa.Integer(), nullable=False),
        sa.Column("acquisitions", sa.Integer(), nullable=True),
        sa.Column("drops", sa.Integer(), nullable=True),
        sa.Column("trades", sa.Integer(), nullable=True),
        sa.Column("acquisition_budget_spent", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["league_season_id"], ["league_seasons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("league_season_id", "espn_team_id", name="uq_teams_season_espn_team"),
    )
    op.create_table(
        "matchups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("matchup_period_id", sa.Integer(), nullable=False),
        sa.Column("home_team_id", sa.Integer(), nullable=False),
        sa.Column("away_team_id", sa.Integer(), nullable=True),
        sa.Column("winner", sa.String(), nullable=False),
        sa.Column("home_categories_won", sa.Integer(), nullable=False),
        sa.Column("home_categories_lost", sa.Integer(), nullable=False),
        sa.Column("categories_tied", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["away_team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["home_team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matchup_period_id"], ["matchup_periods.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("matchup_period_id", "home_team_id", name="uq_matchups_period_home"),
    )
    op.create_table(
        "team_owners",
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_id", "owner_id"),
    )
    op.create_table(
        "roster_slots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("matchup_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.String(), nullable=True),
        sa.Column("pro_team", sa.String(), nullable=True),
        sa.Column("injured", sa.Boolean(), nullable=False),
        sa.Column("injury_status", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["matchup_id"], ["matchups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "matchup_id", "team_id", "player_id", name="uq_roster_slots_matchup_team_player"
        ),
    )


def downgrade() -> None:
    op.drop_table("roster_slots")
    op.drop_table("team_owners")
    op.drop_table("matchups")
    op.drop_table("teams")
    op.drop_table("matchup_periods")
    op.drop_table("players")
    op.drop_table("owners")
