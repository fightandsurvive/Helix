"""
Unattended training daemon.

Flow per generation:
    selfplay → SPRT gate → (adapters every N) → prune → freeze-check → next

Freeze: when SPRT rejects 3x consecutively, OR Elo plateaus over a
5-generation window, OR Elo hits the absolute target — the base is
considered converged. At that point we:
    1. Run adapter training for all six legends.
    2. Run adapter validation.
    3. Idle (no more self-play).

Signals:
    SIGUSR1 — graceful pause. Current stage completes, then exits.
    SIGTERM — hard stop (used by systemd on shutdown).
    Restart with --start-gen N to resume.

Only one orchestrator can run at a time — enforced by the partial unique
index on ops.training_runs WHERE status='running'.
"""
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

from common.db import read_cursor
from config.logging_setup import configure_logging, get_logger
from config.settings import settings
from training.progress import new_run

configure_logging()
log = get_logger(__name__)

LEGENDS = ["morphy", "petrosian", "tal", "kasparov", "fischer", "carlsen"]
_PAUSE = False


def _sig_handler(signum, frame):
    global _PAUSE
    if signum == signal.SIGUSR1:
        _PAUSE = True
        log.warning("orchestrator_pause_requested")


signal.signal(signal.SIGUSR1, _sig_handler)


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def latest_ckpt(checkpoints_dir: Path) -> str:
    base = checkpoints_dir / "distilled.pt"
    gens = sorted(
        checkpoints_dir.glob("rl_gen*.pt"),
        key=lambda p: int(p.stem.split("gen")[1]),
    )
    return str(gens[-1]) if gens else str(base)


def run_stage(cmd: list[str], progress_cb=None, poll: float = 1.0) -> int:
    """Spawn subprocess; poll for completion. Progress callback keeps reporter fresh."""
    proc = subprocess.Popen(cmd)
    while True:
        try:
            return proc.wait(timeout=poll)
        except subprocess.TimeoutExpired:
            if progress_cb:
                progress_cb()
            if _PAUSE:
                proc.terminate()
                log.warning("stage_paused_terminating_child")
                sys.exit(0)


def stage_selfplay(rep, gen: int, cfg: dict, checkpoints_dir: Path):
    target = cfg["games_per_generation"]
    rep.stage_set(gen, "selfplay", 0.0, games_target=target,
                  message=f"generation {gen} self-play")

    def cb():
        with read_cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM selfplay.games WHERE generation=%s", (gen,))
            n = cur.fetchone()[0]
        rep.stage_update(progress=min(n / target, 1.0), games_completed=n)

    cmd = [
        "python", "-m", "training.train_rl",
        "--load", latest_ckpt(checkpoints_dir),
        "--generation", str(gen),
        "--games", str(target),
        "--sims", str(cfg["sims_per_move"]),
        "--save", str(checkpoints_dir / f"rl_gen{gen}.pt"),
    ]
    if cfg.get("use_gumbel"):
        cmd.append("--use-gumbel")

    rc = run_stage(cmd, progress_cb=cb)
    if rc != 0:
        raise RuntimeError(f"selfplay exit code {rc}")
    rep.stage_update(progress=1.0, message="self-play done")


def stage_gate(rep, gen: int, cfg: dict, checkpoints_dir: Path) -> bool:
    rep.stage_set(gen, "gating", 0.0, message="SPRT vs SF")
    gate_name = f"G-auto-gen{gen}"

    def cb():
        with read_cursor() as cur:
            cur.execute(
                """SELECT games_played, status, llr FROM eval.sprt_sprints
                   WHERE gate_name=%s ORDER BY sprint_id DESC LIMIT 1""",
                (gate_name,),
            )
            row = cur.fetchone()
        if row:
            games, status, llr = row
            rep.stage_update(
                progress=min(games / cfg["sprt"]["max_games"], 0.95),
                games_completed=games,
                games_target=cfg["sprt"]["max_games"],
                sprt_llr=llr,
                sprt_status=status,
            )

    rc = run_stage(
        [
            "python", "-m", "training.gate",
            "--gate", gate_name,
            "--ckpt", str(checkpoints_dir / f"rl_gen{gen}.pt"),
            "--ref-name", "sf17",
            "--ref-skill", str(cfg["sprt"]["ref_skill"]),
            "--max-games", str(cfg["sprt"]["max_games"]),
        ],
        progress_cb=cb,
    )

    # Determine outcome by reading the final sprint row
    with read_cursor() as cur:
        cur.execute(
            "SELECT status FROM eval.sprt_sprints WHERE gate_name=%s ORDER BY sprint_id DESC LIMIT 1",
            (gate_name,),
        )
        row = cur.fetchone()
    passed = bool(row and row[0] == "h1_accepted")
    rep.stage_update(
        progress=1.0,
        sprt_status="h1_accepted" if passed else "h0_accepted",
        message="gate passed" if passed else "gate failed",
    )
    return rc == 0 and passed


def stage_adapters(rep, gen: int, cfg: dict, checkpoints_dir: Path):
    rep.stage_set(gen, "adapters", 0.0, message="training adapters")
    base = latest_ckpt(checkpoints_dir)
    for i, legend in enumerate(LEGENDS):
        rep.stage_update(progress=i / len(LEGENDS), message=f"adapter {legend}")
        rc = run_stage(
            [
                "python", "-m", "training.train_adapters",
                "--base", base, "--legend", legend, "--epochs", "5",
            ]
        )
        if rc != 0:
            log.warning("adapter_training_failed", legend=legend, rc=rc)

    # Validation runs once at the end
    run_stage(
        ["python", "-m", "training.validate_adapters", "--base", base]
    )
    rep.stage_update(progress=1.0, message="adapters trained + validated")


def stage_prune(rep, gen: int, cfg: dict):
    from common.db import transaction
    rep.stage_set(gen, "pruning", 0.0, message="pruning old generations")
    with transaction() as cur:
        cur.execute(
            "SELECT selfplay.prune_old_generations(%s, %s, %s)",
            (gen, cfg["prune"]["keep_full"], cfg["prune"]["keep_sampled"]),
        )
    rep.stage_update(progress=1.0, message="prune complete")


def base_converged(gen: int, cfg: dict) -> tuple[bool, str]:
    """Returns (frozen, reason)."""
    # Trigger (a): consecutive SPRT rejects
    threshold = cfg["freeze"]["sprt_rejects_threshold"]
    with read_cursor() as cur:
        cur.execute(
            """SELECT status FROM eval.sprt_sprints
               WHERE gate_name LIKE 'G-auto-gen%%'
               ORDER BY sprint_id DESC LIMIT %s""",
            (threshold,),
        )
        recent = [r[0] for r in cur.fetchall()]
    if len(recent) >= threshold and all(s == "h0_accepted" for s in recent):
        return True, "sprt_rejects"

    # Trigger (b): Elo plateau
    window = cfg["freeze"]["plateau_window"]
    elo_thresh = cfg["freeze"]["plateau_elo_threshold"]
    with read_cursor() as cur:
        cur.execute(
            """SELECT generation, elo_estimate FROM eval.elo_history
               WHERE generation >= %s ORDER BY generation""",
            (gen - window,),
        )
        history = cur.fetchall()
    if len(history) >= window and (history[-1][1] - history[0][1]) < elo_thresh:
        return True, "elo_plateau"

    # Trigger (c): absolute Elo target
    with read_cursor() as cur:
        cur.execute(
            "SELECT elo_estimate FROM eval.elo_history WHERE generation=%s", (gen,)
        )
        row = cur.fetchone()
    if row and row[0] >= cfg["freeze"]["elo_target"]:
        return True, "elo_target"

    return False, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="training/orchestrator.yaml")
    ap.add_argument("--start-gen", type=int, default=1)
    ap.add_argument("--max-gen", type=int, default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)
    checkpoints_dir = settings.checkpoints_dir
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    max_gen = args.max_gen or cfg.get("max_generations", 999)

    gen = args.start_gen
    with new_run(cfg) as rep:
        rep.stage_set(gen, "idle", 1.0, message=f"orchestrator started at gen {gen}")
        frozen = False
        while gen <= max_gen and not frozen:
            log.info("generation_start", gen=gen)
            stage_selfplay(rep, gen, cfg, checkpoints_dir)

            gate_passed = stage_gate(rep, gen, cfg, checkpoints_dir)
            if not gate_passed:
                log.warning("generation_failed_gate", gen=gen)
                # Advance regardless — SPRT rejects count toward freeze detection
                gen += 1
                continue

            if gen % cfg["retrain_adapters_every"] == 0:
                stage_adapters(rep, gen, cfg, checkpoints_dir)

            if gen >= cfg["prune"]["after_generations"]:
                stage_prune(rep, gen, cfg)

            # Freeze check
            frozen, reason = base_converged(gen, cfg)
            if frozen:
                log.info("base_frozen", generation=gen, reason=reason)
                rep.stage_set(gen, "adapters", 0.0, message=f"freeze: {reason} — training final adapters")
                stage_adapters(rep, gen, cfg, checkpoints_dir)
                rep.stage_set(gen, "idle", 1.0,
                              message=f"frozen at gen {gen} ({reason}); run complete")
                break

            gen += 1

        if not frozen:
            rep.stage_set(gen - 1, "idle", 1.0, message="all generations complete")


if __name__ == "__main__":
    main()
