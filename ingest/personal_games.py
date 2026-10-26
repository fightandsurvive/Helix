"""
Import personal games from Chess.com or Lichess.

Both platforms use public APIs (no auth required):
    Chess.com: /pub/player/{u}/games/archives → monthly archive URLs
    Lichess:   /api/games/user/{u} → streaming NDJSON

The full import flow:
    1. Ensure a player_profile row exists
    2. Fetch and store games (idempotent via (profile_id, external_id))
    3. Inline gambit detection (insert into analytics.gambit_plays)
    4. Queue accuracy jobs (analytics.accuracy_jobs)
"""

from __future__ import annotations

import argparse
import io
import json
import time

import chess
import chess.pgn
import requests

from common.db import transaction
from config.logging_setup import configure_logging, get_logger
from engine.gambit_matcher import find_gambits, load_gambit_index_from_db, moves_from_pgn

configure_logging()
log = get_logger(__name__)

UA = {"User-Agent": "Helix-Chess/0.1 (local)"}


def _ensure_profile(platform: str, username: str) -> int:
    """Create the profile if missing, return its ID."""
    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO app.player_profiles (platform, username)
            VALUES (%s, %s)
            ON CONFLICT (platform, username) DO NOTHING
            """,
            (platform, username),
        )
        cur.execute(
            "SELECT profile_id FROM app.player_profiles WHERE platform=%s AND username=%s",
            (platform, username),
        )
        return cur.fetchone()[0]


def _int_or_none(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _store_game(
    profile_id: int,
    platform: str,
    external_id: str,
    pgn_text: str,
    username: str,
    end_time: int | None,
    tc: str | None,
    gambit_index: dict,
) -> int:
    """Parse a PGN and store it. Returns 1 if inserted, 0 if skipped."""
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return 0
    h = game.headers

    white = h.get("White", "")
    black = h.get("Black", "")
    user_color = "white" if username.lower() == white.lower() else "black"
    user_rating = _int_or_none(h.get("WhiteElo") if user_color == "white" else h.get("BlackElo"))
    opp_rating = _int_or_none(h.get("BlackElo") if user_color == "white" else h.get("WhiteElo"))

    result_raw = h.get("Result", "*")
    if result_raw == "1/2-1/2":
        result = "draw"
    elif (result_raw == "1-0" and user_color == "white") or (result_raw == "0-1" and user_color == "black"):
        result = "win"
    else:
        result = "loss"

    eco = h.get("ECO", None)
    opening = h.get("Opening", None)

    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO app.personal_games
                (profile_id, platform, external_id, played_at,
                 white_player, black_player, user_color,
                 user_rating, opponent_rating, result, time_control,
                 eco_code, opening_name, pgn)
            VALUES (%s, %s, %s, to_timestamp(%s),
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (profile_id, external_id) DO NOTHING
            RETURNING game_id
            """,
            (
                profile_id,
                platform,
                external_id,
                end_time or 0,
                white,
                black,
                user_color,
                user_rating,
                opp_rating,
                result,
                tc,
                eco,
                opening,
                pgn_text,
            ),
        )
        row = cur.fetchone()
        if not row:
            return 0  # already imported
        game_id = row[0]

        # Inline gambit detection
        moves = moves_from_pgn(pgn_text)
        for hit in find_gambits(moves, gambit_index):
            if hit.side != user_color:
                continue
            participant = username
            opponent = black if user_color == "white" else white
            cur.execute(
                """
                INSERT INTO analytics.gambit_plays
                    (source, ref_id, participant, participant_type, opponent,
                     color, gambit_id, gambit_depth, result, played_at)
                VALUES ('personal', %s, %s, 'human', %s, %s, %s, %s, %s, to_timestamp(%s))
                ON CONFLICT DO NOTHING
                """,
                (
                    game_id,
                    participant,
                    opponent,
                    user_color,
                    hit.gambit_id,
                    hit.depth,
                    result,
                    end_time or 0,
                ),
            )

        # Queue accuracy job
        cur.execute(
            """
            INSERT INTO analytics.accuracy_jobs (source, ref_id, priority)
            VALUES ('personal', %s, 5)
            ON CONFLICT DO NOTHING
            """,
            (game_id,),
        )
    return 1


def import_chesscom(username: str, profile_id: int, since_year: int = 2015) -> int:
    gambit_index = load_gambit_index_from_db()
    r = requests.get(
        f"https://api.chess.com/pub/player/{username}/games/archives",
        headers=UA,
        timeout=30,
    )
    r.raise_for_status()
    archives = r.json().get("archives", [])
    n = 0

    for url in archives:
        parts = url.rsplit("/", 2)
        try:
            if int(parts[-2]) < since_year:
                continue
        except (ValueError, IndexError):
            continue
        resp = requests.get(url, headers=UA, timeout=60)
        if resp.status_code != 200:
            continue
        for g in resp.json().get("games", []):
            pgn_text = g.get("pgn")
            if not pgn_text:
                continue
            n += _store_game(
                profile_id,
                "chesscom",
                str(g.get("uuid", g.get("url", ""))),
                pgn_text,
                username,
                g.get("end_time"),
                g.get("time_class"),
                gambit_index,
            )
        time.sleep(0.1)  # polite to the API
    return n


def import_lichess(username: str, profile_id: int, max_games: int = 5000) -> int:
    gambit_index = load_gambit_index_from_db()
    r = requests.get(
        f"https://lichess.org/api/games/user/{username}",
        headers={**UA, "Accept": "application/x-ndjson"},
        params={
            "max": max_games,
            "clocks": "false",
            "evals": "false",
            "opening": "true",
            "accuracy": "false",
        },
        stream=True,
        timeout=60,
    )
    if r.status_code == 404:
        raise SystemExit(
            f"User '{username}' not found on Lichess. "
            f"Check the spelling — Lichess usernames are case-sensitive.\n"
            f"Test the URL in a browser: https://lichess.org/@/{username}"
        )
    r.raise_for_status()
    n = 0
    for line in r.iter_lines():
        if not line:
            continue
        try:
            game = json.loads(line)
        except json.JSONDecodeError:
            continue
        pgn_text = game.get("pgn")
        if not pgn_text:
            continue
        n += _store_game(
            profile_id,
            "lichess",
            game.get("id", ""),
            pgn_text,
            username,
            game.get("createdAt", 0) // 1000,
            game.get("speed"),
            gambit_index,
        )
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["chesscom", "lichess"], required=True)
    ap.add_argument("--username", required=True)
    ap.add_argument("--since-year", type=int, default=2015)
    ap.add_argument("--max-games", type=int, default=5000)
    args = ap.parse_args()

    profile_id = _ensure_profile(args.platform, args.username)
    log.info("profile_ready", profile_id=profile_id, platform=args.platform)

    if args.platform == "chesscom":
        n = import_chesscom(args.username, profile_id, args.since_year)
    else:
        n = import_lichess(args.username, profile_id, args.max_games)

    log.info("personal_import_done", platform=args.platform, games=n, profile_id=profile_id)


if __name__ == "__main__":
    main()
