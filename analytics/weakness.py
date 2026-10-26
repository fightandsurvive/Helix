"""
Weakness Finder — detect recurring patterns from per-move loss data.

Each detector is a function that takes a profile_id, queries
analytics.move_losses + app.personal_games, and returns a list of
Insight objects. `run_all` collects, deduplicates, ranks, and persists.

Design constraints:
- Every detector must have a minimum sample size (game count or move
  count) to prevent noise on small datasets.
- Every insight must include (a) the raw metric, (b) the baseline for
  comparison, (c) statistical confidence, (d) a suggested action that
  links to an existing Helix feature.
- Impact score (0-1) combines the *magnitude* of the deficit with the
  *frequency* of the pattern. A 10% deficit affecting 40% of your games
  is more valuable than a 30% deficit affecting 3%.

Reruns are idempotent: `run_all` deletes prior insights for the profile
before re-inserting.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from common.db import read_cursor, transaction
from config.logging_setup import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)


# ── Insight data model ──────────────────────────────────────────────
@dataclass
class Insight:
    category: str
    severity: str  # 'critical' | 'high' | 'medium' | 'low'
    title: str
    description: str
    metric_name: str
    metric_value: float
    baseline_value: float | None
    sample_size: int
    confidence: float  # 0-1
    impact_score: float  # 0-1
    action_type: str
    action_label: str
    action_payload: dict[str, Any] | None
    evidence: dict[str, Any] | None

    def to_dict(self) -> dict:
        return asdict(self)


# ── Utility ─────────────────────────────────────────────────────────
def _severity_from_ratio(ratio: float) -> str:
    """Map 'how much worse than baseline' to a severity label."""
    if ratio >= 2.0:
        return "critical"
    if ratio >= 1.5:
        return "high"
    if ratio >= 1.2:
        return "medium"
    return "low"


def _confidence_from_sample(n: int, thresholds: tuple[int, int, int] = (50, 200, 1000)) -> float:
    """Rough confidence scaling by sample size. Not a rigorous CI but
    honest about how much data backs each claim."""
    if n >= thresholds[2]:
        return 0.95
    if n >= thresholds[1]:
        return 0.85
    if n >= thresholds[0]:
        return 0.7
    return 0.5


# ── Detector 1: Phase weakness ──────────────────────────────────────
def analyze_phases(profile_id: int) -> list[Insight]:
    """Compare average centipawn loss per phase to the player's baseline."""
    with read_cursor() as cur:
        cur.execute(
            """
            SELECT ml.phase, AVG(ml.loss_cp)::REAL, COUNT(*)::INT
            FROM analytics.move_losses ml
            JOIN app.personal_games g ON g.game_id = ml.game_id
            WHERE g.profile_id = %s
            GROUP BY ml.phase
            HAVING COUNT(*) >= 30
            """,
            (profile_id,),
        )
        rows = cur.fetchall()

    if len(rows) < 2:
        return []

    total_weighted = sum(r[1] * r[2] for r in rows)
    total_count = sum(r[2] for r in rows)
    baseline = total_weighted / total_count

    insights = []
    for phase, avg_loss, count in rows:
        ratio = avg_loss / baseline if baseline > 0 else 1.0
        if ratio < 1.2:
            continue

        game_share = count / total_count
        impact = min(1.0, (ratio - 1.0) * game_share * 2.5)

        insights.append(
            Insight(
                category="phase",
                severity=_severity_from_ratio(ratio),
                title=f"Your {phase.lower()} play is a weak point",
                description=(
                    f"Your average loss in the {phase.lower()} is {avg_loss:.0f} centipawns — "
                    f"{int((ratio - 1) * 100)}% worse than your overall average of {baseline:.0f}. "
                    f"Across {count} moves, this is a consistent pattern worth targeting."
                ),
                metric_name=f"{phase} ACPL",
                metric_value=round(avg_loss, 1),
                baseline_value=round(baseline, 1),
                sample_size=count,
                confidence=_confidence_from_sample(count, (100, 300, 1000)),
                impact_score=round(impact, 3),
                action_type="practice_phase",
                action_label=f"Practice {phase.lower()} puzzles",
                action_payload={"phase": phase},
                evidence={
                    "rows": [{"label": p, "value": round(a, 1), "count": c} for p, a, c in rows],
                    "baseline": round(baseline, 1),
                },
            )
        )

    return insights


# ── Detector 2: Time-of-game weakness ──────────────────────────────
def analyze_move_bands(profile_id: int) -> list[Insight]:
    """Identify if the player's accuracy degrades at specific move ranges."""
    with read_cursor() as cur:
        cur.execute(
            """
            SELECT
                CASE
                    WHEN ml.ply_number <= 10 THEN '1-5 moves'
                    WHEN ml.ply_number <= 20 THEN '6-10 moves'
                    WHEN ml.ply_number <= 40 THEN '11-20 moves'
                    WHEN ml.ply_number <= 60 THEN '21-30 moves'
                    ELSE '31+ moves'
                END AS band,
                AVG(ml.loss_cp)::REAL,
                COUNT(*)::INT
            FROM analytics.move_losses ml
            JOIN app.personal_games g ON g.game_id = ml.game_id
            WHERE g.profile_id = %s
            GROUP BY band
            HAVING COUNT(*) >= 30
            """,
            (profile_id,),
        )
        rows = cur.fetchall()

    if len(rows) < 2:
        return []

    total_weighted = sum(r[1] * r[2] for r in rows)
    total_count = sum(r[2] for r in rows)
    baseline = total_weighted / total_count

    insights = []
    for band, avg_loss, count in rows:
        ratio = avg_loss / baseline if baseline > 0 else 1.0
        if ratio < 1.25:
            continue

        game_share = count / total_count
        impact = min(1.0, (ratio - 1.0) * game_share * 2.0)

        insights.append(
            Insight(
                category="move_band",
                severity=_severity_from_ratio(ratio),
                title=f"You lose focus around {band}",
                description=(
                    f"Your average loss in {band} of the game is {avg_loss:.0f} cp — "
                    f"{int((ratio - 1) * 100)}% worse than your overall average. "
                    f"This is likely fatigue or time-trouble leakage."
                ),
                metric_name=f"ACPL at {band}",
                metric_value=round(avg_loss, 1),
                baseline_value=round(baseline, 1),
                sample_size=count,
                confidence=_confidence_from_sample(count, (100, 300, 1000)),
                impact_score=round(impact, 3),
                action_type="review_games",
                action_label="Review late-game positions",
                action_payload={"band": band},
                evidence={
                    "rows": [{"label": b, "value": round(a, 1), "count": c} for b, a, c in rows],
                    "baseline": round(baseline, 1),
                },
            )
        )

    return insights


# ── Detector 3: Opening strength ────────────────────────────────────
def analyze_openings(profile_id: int) -> list[Insight]:
    """Flag openings the player underperforms in, by color."""
    insights = []

    for color in ("white", "black"):
        with read_cursor() as cur:
            cur.execute(
                """
                SELECT eco_code, MIN(opening_name), COUNT(*)::INT,
                       SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END)::INT,
                       AVG(accuracy)::REAL
                FROM app.personal_games g
                WHERE g.profile_id = %s AND g.eco_code IS NOT NULL AND g.user_color = %s
                GROUP BY eco_code
                HAVING COUNT(*) >= 5
                ORDER BY COUNT(*) DESC
                """,
                (profile_id, color),
            )
            rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    COALESCE(
                        SUM(CASE WHEN result='win' THEN 1 ELSE 0 END)::REAL / NULLIF(COUNT(*), 0),
                        0.5
                    )
                FROM app.personal_games
                WHERE profile_id = %s AND user_color = %s
                """,
                (profile_id, color),
            )
            baseline_score = cur.fetchone()[0] or 0.5

        for eco, opening, games, wins, accuracy in rows:
            score = wins / games if games else 0.5
            if score >= baseline_score * 0.85:
                continue

            deficit = (baseline_score - score) / baseline_score if baseline_score > 0 else 0
            impact = min(1.0, deficit * (games / 15))

            insights.append(
                Insight(
                    category=f"opening_{color}",
                    severity="high" if deficit > 0.4 else "medium",
                    title=f"Trouble with {opening} as {color}",
                    description=(
                        f"Across {games} games you score {score*100:.0f}% with this opening — "
                        f"against your baseline of {baseline_score*100:.0f}% as {color}. "
                        f"Average accuracy in these games: {accuracy:.0f}%."
                    ),
                    metric_name="Score rate",
                    metric_value=round(score, 3),
                    baseline_value=round(baseline_score, 3),
                    sample_size=games,
                    confidence=_confidence_from_sample(games, (5, 10, 30)),
                    impact_score=round(impact, 3),
                    action_type="train_opening",
                    action_label=f"Study {opening}",
                    action_payload={"eco_code": eco, "color": color},
                    evidence={
                        "rows": [
                            {"label": r[1], "value": round(r[3] / r[2], 3), "count": r[2]} for r in rows
                        ],
                        "baseline": round(baseline_score, 3),
                    },
                )
            )

    return insights


# ── Detector 4: Piece-specific blunders ─────────────────────────────
def analyze_pieces(profile_id: int) -> list[Insight]:
    """Identify which piece type correlates with the most blunders."""
    with read_cursor() as cur:
        cur.execute(
            """
            SELECT ml.piece_moved,
                   COUNT(*)::INT,
                   SUM(CASE WHEN ml.loss_cp >= 200 THEN 1 ELSE 0 END)::INT,
                   AVG(ml.loss_cp)::REAL
            FROM analytics.move_losses ml
            JOIN app.personal_games g ON g.game_id = ml.game_id
            WHERE g.profile_id = %s
            GROUP BY ml.piece_moved
            HAVING COUNT(*) >= 100
            """,
            (profile_id,),
        )
        rows = cur.fetchall()

    if len(rows) < 2:
        return []

    total_moves = sum(r[1] for r in rows)
    total_blunders = sum(r[2] for r in rows)
    baseline_rate = total_blunders / total_moves if total_moves else 0
    if baseline_rate == 0:
        return []

    piece_names = {
        "P": "pawns",
        "N": "knights",
        "B": "bishops",
        "R": "rooks",
        "Q": "queens",
        "K": "king",
    }

    insights = []
    for piece, total, blunders, _avg in rows:
        rate = blunders / total
        ratio = rate / baseline_rate if baseline_rate > 0 else 1.0
        if ratio < 1.5 or blunders < 5:
            continue

        impact = min(1.0, (ratio - 1.0) * (total / 800))
        piece_name = piece_names.get(piece.upper(), piece)

        insights.append(
            Insight(
                category="piece",
                severity=_severity_from_ratio(ratio),
                title=f"You blunder {piece_name} more than other pieces",
                description=(
                    f"{rate*100:.1f}% of your moves with {piece_name} are blunders "
                    f"(200+ cp loss) — {ratio:.1f}× your overall blunder rate of "
                    f"{baseline_rate*100:.1f}%. Across {total} moves, this is a "
                    f"concrete pattern to drill."
                ),
                metric_name="Blunder rate",
                metric_value=round(rate, 4),
                baseline_value=round(baseline_rate, 4),
                sample_size=total,
                confidence=_confidence_from_sample(total, (200, 500, 1500)),
                impact_score=round(impact, 3),
                action_type="practice_puzzles",
                action_label="Practice hanging-piece puzzles",
                action_payload={"theme": "hangingPiece"},
                evidence={
                    "rows": [{"label": r[0], "value": round(r[2] / r[1], 4), "count": r[1]} for r in rows],
                    "baseline": round(baseline_rate, 4),
                },
            )
        )

    return insights


# ── Detector 5: Board region bias ───────────────────────────────────
def analyze_regions(profile_id: int) -> list[Insight]:
    """Identify whether kingside or queenside play correlates with losses."""
    with read_cursor() as cur:
        cur.execute(
            """
            SELECT ml.board_region,
                   AVG(ml.loss_cp)::REAL,
                   COUNT(*)::INT
            FROM analytics.move_losses ml
            JOIN app.personal_games g ON g.game_id = ml.game_id
            WHERE g.profile_id = %s
            GROUP BY ml.board_region
            HAVING COUNT(*) >= 100
            """,
            (profile_id,),
        )
        rows = cur.fetchall()

    if len(rows) < 2:
        return []

    total_weighted = sum(r[1] * r[2] for r in rows)
    total_count = sum(r[2] for r in rows)
    baseline = total_weighted / total_count

    insights = []
    for region, avg_loss, count in rows:
        ratio = avg_loss / baseline if baseline > 0 else 1.0
        if ratio < 1.2:
            continue

        game_share = count / total_count
        impact = min(1.0, (ratio - 1.0) * game_share * 2.0)

        insights.append(
            Insight(
                category="region",
                severity=_severity_from_ratio(ratio),
                title=f"You play weaker on the {region}",
                description=(
                    f"Your average loss on the {region} is {avg_loss:.0f} cp — "
                    f"{int((ratio - 1) * 100)}% worse than your average elsewhere. "
                    f"This may reflect an attention bias or unfamiliar pawn structures."
                ),
                metric_name=f"{region.title()} ACPL",
                metric_value=round(avg_loss, 1),
                baseline_value=round(baseline, 1),
                sample_size=count,
                confidence=_confidence_from_sample(count, (200, 500, 1500)),
                impact_score=round(impact, 3),
                action_type="practice_puzzles",
                action_label=f"Practice {region} tactics",
                action_payload={"region": region},
                evidence={
                    "rows": [{"label": r[0], "value": round(r[1], 1), "count": r[2]} for r in rows],
                    "baseline": round(baseline, 1),
                },
            )
        )

    return insights


# ── Detector 6: Color asymmetry ─────────────────────────────────────
def analyze_color(profile_id: int) -> list[Insight]:
    """Compare white vs black performance."""
    with read_cursor() as cur:
        cur.execute(
            """
            SELECT user_color,
                   COUNT(*)::INT,
                   SUM(CASE WHEN result='win' THEN 1 ELSE 0 END)::INT,
                   AVG(accuracy)::REAL
            FROM app.personal_games
            WHERE profile_id = %s AND accuracy IS NOT NULL
            GROUP BY user_color
            """,
            (profile_id,),
        )
        rows = cur.fetchall()

    if len(rows) != 2:
        return []

    stats = {r[0]: {"games": r[1], "wins": r[2], "acc": r[3]} for r in rows}
    if "white" not in stats or "black" not in stats:
        return []
    if stats["white"]["games"] < 10 or stats["black"]["games"] < 10:
        return []

    w_acc = stats["white"]["acc"]
    b_acc = stats["black"]["acc"]
    if w_acc is None or b_acc is None:
        return []

    diff = abs(w_acc - b_acc)
    if diff < 5:
        return []

    better_color = "white" if w_acc > b_acc else "black"
    worse_color = "black" if w_acc > b_acc else "white"
    worse_acc = min(w_acc, b_acc)
    better_acc = max(w_acc, b_acc)

    insights = [
        Insight(
            category="color",
            severity="high" if diff > 10 else "medium",
            title=f"You play weaker as {worse_color}",
            description=(
                f"Your accuracy as {better_color} is {better_acc:.1f}%, versus "
                f"{worse_acc:.1f}% as {worse_color} — a {diff:.1f}-point gap across "
                f"{stats[worse_color]['games']} games as {worse_color}. "
                f"Consider focusing your opening repertoire on the weaker color."
            ),
            metric_name=f"{worse_color.title()} accuracy",
            metric_value=round(worse_acc, 1),
            baseline_value=round(better_acc, 1),
            sample_size=stats[worse_color]["games"],
            confidence=_confidence_from_sample(stats[worse_color]["games"], (10, 30, 100)),
            impact_score=round(min(1.0, diff / 20), 3),
            action_type="train_opening",
            action_label=f"Study more {worse_color} openings",
            action_payload={"color": worse_color},
            evidence={
                "rows": [
                    {"label": "White", "value": round(w_acc, 1), "count": stats["white"]["games"]},
                    {"label": "Black", "value": round(b_acc, 1), "count": stats["black"]["games"]},
                ],
                "baseline": round(better_acc, 1),
            },
        )
    ]

    return insights


# ── Detector 7: Rating-band performance ─────────────────────────────
def analyze_rating(profile_id: int) -> list[Insight]:
    """Show win rate by opponent rating bucket — where does the player plateau?"""
    with read_cursor() as cur:
        cur.execute(
            """
            SELECT
                CASE
                    WHEN opponent_rating < 1000 THEN '<1000'
                    WHEN opponent_rating < 1200 THEN '1000-1199'
                    WHEN opponent_rating < 1400 THEN '1200-1399'
                    WHEN opponent_rating < 1600 THEN '1400-1599'
                    WHEN opponent_rating < 1800 THEN '1600-1799'
                    WHEN opponent_rating < 2000 THEN '1800-1999'
                    ELSE '2000+'
                END AS bucket,
                COUNT(*)::INT,
                SUM(CASE WHEN result='win' THEN 1 ELSE 0 END)::INT
            FROM app.personal_games
            WHERE profile_id = %s AND opponent_rating IS NOT NULL
            GROUP BY bucket
            HAVING COUNT(*) >= 10
            """,
            (profile_id,),
        )
        rows = cur.fetchall()

    if len(rows) < 2:
        return []

    # Sort buckets by rating order
    order = ["<1000", "1000-1199", "1200-1399", "1400-1599", "1600-1799", "1800-1999", "2000+"]
    rows = sorted(rows, key=lambda r: order.index(r[0]))

    insights = []
    # Find the bucket where win rate drops most sharply from the previous
    for i in range(1, len(rows)):
        prev_bucket, prev_games, prev_wins = rows[i - 1]
        cur_bucket, cur_games, cur_wins = rows[i]
        prev_rate = prev_wins / prev_games
        cur_rate = cur_wins / cur_games
        drop = prev_rate - cur_rate
        if drop < 0.25:  # less than 25 percentage point drop
            continue

        insights.append(
            Insight(
                category="rating",
                severity="high" if drop > 0.4 else "medium",
                title=f"Win rate drops sharply at {cur_bucket}",
                description=(
                    f"Against players rated {prev_bucket} you win {prev_rate*100:.0f}%, "
                    f"but against {cur_bucket} you win only {cur_rate*100:.0f}% — "
                    f"a {drop*100:.0f}-point drop. This suggests your play has plateaued "
                    f"at the level below {cur_bucket}."
                ),
                metric_name="Win rate",
                metric_value=round(cur_rate, 3),
                baseline_value=round(prev_rate, 3),
                sample_size=cur_games,
                confidence=_confidence_from_sample(cur_games, (10, 30, 100)),
                impact_score=round(min(1.0, drop), 3),
                action_type="review_games",
                action_label=f"Review losses vs {cur_bucket}",
                action_payload={"rating_range": cur_bucket},
                evidence={
                    "rows": [{"label": r[0], "value": round(r[2] / r[1], 3), "count": r[1]} for r in rows],
                },
            )
        )
        # Only report the sharpest drop, not every drop
        break

    return insights


# ── Detector 8: Improvement trend ───────────────────────────────────
def analyze_trend(profile_id: int) -> list[Insight]:
    """Compare accuracy of the most recent 50 games to the prior 50."""
    with read_cursor() as cur:
        cur.execute(
            """
            SELECT accuracy FROM app.personal_games
            WHERE profile_id = %s AND accuracy IS NOT NULL AND played_at IS NOT NULL
            ORDER BY played_at DESC
            LIMIT 100
            """,
            (profile_id,),
        )
        rows = cur.fetchall()

    if len(rows) < 50:
        return []

    recent = [r[0] for r in rows[:50]]
    previous = [r[0] for r in rows[50:100]] if len(rows) >= 100 else []

    if not previous:
        return []

    recent_avg = sum(recent) / len(recent)
    prev_avg = sum(previous) / len(previous)
    delta = recent_avg - prev_avg

    if abs(delta) < 2:
        return []

    improving = delta > 0
    insights = [
        Insight(
            category="trend",
            severity="low",
            title="Accuracy is " + ("improving" if improving else "regressing"),
            description=(
                f"Your last 50 games average {recent_avg:.1f}% accuracy, "
                f"{abs(delta):.1f} points {'higher' if improving else 'lower'} than "
                f"the prior 50 ({prev_avg:.1f}%). "
                + (
                    "Keep doing what you're doing."
                    if improving
                    else "Consider slowing down or focusing on fewer openings."
                )
            ),
            metric_name="Recent accuracy",
            metric_value=round(recent_avg, 1),
            baseline_value=round(prev_avg, 1),
            sample_size=100,
            confidence=0.85,
            impact_score=round(min(1.0, abs(delta) / 10), 3),
            action_type="custom",
            action_label="View trend chart",
            action_payload={"metric": "accuracy"},
            evidence={
                "rows": [
                    {"label": "Recent 50", "value": round(recent_avg, 1), "count": 50},
                    {"label": "Prior 50", "value": round(prev_avg, 1), "count": 50},
                ],
            },
        )
    ]

    return insights


# ── Runner ──────────────────────────────────────────────────────────
def run_all(profile_id: int) -> list[Insight]:
    """
    Run all detectors, deduplicate, rank by impact, persist to cache.
    """
    log.info("weakness_run_start", profile_id=profile_id)

    detectors = [
        ("phases", analyze_phases),
        ("move_bands", analyze_move_bands),
        ("openings", analyze_openings),
        ("pieces", analyze_pieces),
        ("regions", analyze_regions),
        ("color", analyze_color),
        ("rating", analyze_rating),
        ("trend", analyze_trend),
    ]

    all_insights: list[Insight] = []
    for name, detector in detectors:
        try:
            found = detector(profile_id)
            all_insights.extend(found)
            log.info("detector_done", detector=name, found=len(found))
        except Exception as e:
            log.exception("detector_failed", detector=name, error=str(e))

    # Deduplicate by (category, title) — safety net against reruns
    seen = set()
    unique: list[Insight] = []
    for ins in all_insights:
        key = (ins.category, ins.title)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ins)

    # Rank by impact × confidence (a low-confidence insight of high impact
    # is still valuable, but bounded by its confidence)
    unique.sort(key=lambda i: i.impact_score * i.confidence, reverse=True)

    # Persist
    with transaction() as cur:
        cur.execute(
            "DELETE FROM analytics.weakness_insights WHERE profile_id = %s",
            (profile_id,),
        )
        from psycopg.types.json import Json

        for ins in unique:
            cur.execute(
                """
                INSERT INTO analytics.weakness_insights
                    (profile_id, category, severity, title, description,
                     metric_name, metric_value, baseline_value, sample_size,
                     confidence, impact_score, action_type, action_label,
                     action_payload, evidence)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    profile_id,
                    ins.category,
                    ins.severity,
                    ins.title,
                    ins.description,
                    ins.metric_name,
                    ins.metric_value,
                    ins.baseline_value,
                    ins.sample_size,
                    ins.confidence,
                    ins.impact_score,
                    ins.action_type,
                    ins.action_label,
                    Json(ins.action_payload) if ins.action_payload else None,
                    Json(ins.evidence) if ins.evidence else None,
                ),
            )

    log.info("weakness_run_done", profile_id=profile_id, insights=len(unique))
    return unique


def refresh_for_all_profiles() -> dict[int, int]:
    """Refresh insights for every profile that has ≥1 game. Returns
    {profile_id: insight_count}."""
    with read_cursor() as cur:
        cur.execute("SELECT DISTINCT profile_id FROM app.personal_games")
        profiles = [r[0] for r in cur.fetchall()]

    results = {}
    for pid in profiles:
        try:
            insights = run_all(pid)
            results[pid] = len(insights)
        except Exception as e:
            log.exception("profile_refresh_failed", profile_id=pid, error=str(e))
            results[pid] = -1
    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        results = refresh_for_all_profiles()
        print(json.dumps(results))
    elif len(sys.argv) > 1:
        profile_id = int(sys.argv[1])
        insights = run_all(profile_id)
        print(json.dumps([i.to_dict() for i in insights], indent=2))
    else:
        print("usage: python -m analytics.weakness <profile_id> | --all")
        sys.exit(1)
