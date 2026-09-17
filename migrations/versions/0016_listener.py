"""listener

The in-season listener (docs/pickups.md, layer 1): a player's status and
ownership as a time series, who was available in the league at each pass,
the NBA schedule, the news the passes fetched, and the events diffed from
consecutive snapshots.

Nothing to backfill. ESPN serves status as of the request only, so the
history starts the day the first pass runs.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_status_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pass_label", sa.String(), nullable=False),
        sa.Column("injury_status", sa.String(), nullable=True),
        sa.Column("injured", sa.Boolean(), nullable=False),
        sa.Column("expected_return_date", sa.Date(), nullable=True),
        sa.Column("pro_team_id", sa.Integer(), nullable=True),
        sa.Column("on_team_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("percent_owned", sa.Float(), nullable=True),
        sa.Column("percent_change", sa.Float(), nullable=True),
        sa.Column("percent_started", sa.Float(), nullable=True),
        sa.Column("auction_value_average", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_player_status_snapshots_player_observed",
        "player_status_snapshots",
        ["player_id", "observed_at"],
    )
    op.create_index(
        "ix_player_status_snapshots_season_observed",
        "player_status_snapshots",
        ["season", "observed_at"],
    )
    op.create_table(
        "player_news",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("published", sa.DateTime(timezone=True), nullable=False),
        sa.Column("headline", sa.String(), nullable=False),
        sa.Column("story", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "published", "headline", name="uq_player_news_story"),
    )
    op.create_table(
        "free_agent_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_season_id", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scoring_period", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("waiver_clears_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["league_season_id"], ["league_seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_free_agent_snapshots_season_observed",
        "free_agent_snapshots",
        ["league_season_id", "observed_at"],
    )
    op.create_table(
        "pro_team_games",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("pro_team_id", sa.Integer(), nullable=False),
        sa.Column("scoring_period", sa.Integer(), nullable=False),
        sa.Column("game_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opponent_pro_team_id", sa.Integer(), nullable=False),
        sa.Column("home", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "season", "pro_team_id", "scoring_period", name="uq_pro_team_games_key"
        ),
    )
    op.create_table(
        "player_status_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("current", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "kind", "observed_at", name="uq_player_status_events_key"),
    )
    op.create_index(
        "ix_player_status_events_season_observed",
        "player_status_events",
        ["season", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_player_status_events_season_observed", table_name="player_status_events")
    op.drop_table("player_status_events")
    op.drop_table("pro_team_games")
    op.drop_index("ix_free_agent_snapshots_season_observed", table_name="free_agent_snapshots")
    op.drop_table("free_agent_snapshots")
    op.drop_table("player_news")
    op.drop_index(
        "ix_player_status_snapshots_season_observed", table_name="player_status_snapshots"
    )
    op.drop_index(
        "ix_player_status_snapshots_player_observed", table_name="player_status_snapshots"
    )
    op.drop_table("player_status_snapshots")
