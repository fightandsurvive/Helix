"""
Recompute gambit plays for a personal profile.

Used by import flow (as a repair/recheck step) and by backfill_analytics.
The primary gambit detection happens inline during import — this script
is a fallback for catching data that was imported before gambit detection
was enabled, or for full recomputation after a gambit registry update.

Also refreshes the analytics materialized views after recomputing.
"""
from __future__ import annotations

import argparse

from common.db import read_cursor, transaction
from config.logging_setup import configure_logging, get_logger
from engine.gambit_matcher import find_gambits, load_gambit_index_from_db, moves_from_pgn

configure_logging()
log = get_logger(__name__)


def compute_for_profile(profile_id: int, gambit_index: dict) -> int:
    """
    Insert missing gambit_plays for all personal games in this profile.
    Idempotent — ON CONFLICT DO NOTHING skips already-recorded hits.
    """
    with read_cursor() as cur:
        cur.execute(
            """
            SELECT g.game_id, g.pgn, g.white_player, g.black_player,
                   g.user_color, g.result, g.played_at
            FROM app.personal_games g
            WHERE g.profile_id = %s
            """,
            (profile_id,),
        )
        games = cur.fetchall()

    n = 0
    for gid, pgn, w, b, ucolor, result, played_at in games:
        moves = moves_from_pgn(pgn or "")
        if not moves:
            continue
        for hit in find_gambits(moves, gambit_index):
            # Credit only if the user was the gambit-playing side
            if hit.side != ucolor:
                continue
            participant = w if ucolor == "white" else b
            opponent = b if ucolor == "white" else w
            with transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO analytics.gambit_plays
                        (source, ref_id, participant, participant_type, opponent,
                         color, gambit_id, gambit_depth, result, played_at)
                    VALUES ('personal', %s, %s, 'human', %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        gid, participant, opponent, ucolor,
                        hit.gambit_id, hit.depth, result, played_at,
                    ),
                )
            n += 1
    return n


def refresh_views() -> None:
    """Refresh the analytics materialized views after gambit changes."""
    with transaction() as cur:
        cur.execute("SELECT analytics.refresh_views()")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile-id", type=int, required=True)
    args = ap.parse_args()

    index = load_gambit_index_from_db()
    n = compute_for_profile(args.profile_id, index)
    refresh_views()
    log.info("gambit_stats_computed", profile_id=args.profile_id, plays=n)


if __name__ == "__main__":
    main()
