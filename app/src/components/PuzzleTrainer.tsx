import { useEffect, useState } from "react";
import { Chessboard } from "react-chessboard";
import { Chess } from "chess.js";
import { useInvoke } from "../hooks/useInvoke";
import { invoke } from "@tauri-apps/api/core";
import { BOARD_DARK, BOARD_LIGHT } from "./ChessBoardView";

interface Puzzle {
    puzzle_id: string;
    fen: string;
    solution_uci: string;
    rating?: number;
    themes: string[];
    source: string;
    eco_code?: string;
    phase_flag?: string;
}

export const PuzzleTrainer = () => {
    const [phase, setPhase] = useState("");
    const [selectedTheme, setSelectedTheme] = useState<string>("");
    const { data: themes } = useInvoke<[string, number][]>("get_puzzle_themes");
    const { data: puzzles, loading } = useInvoke<Puzzle[]>(
        "get_due_puzzles",
        { limit: 10, phase: phase || null, theme: selectedTheme || null },
        { deps: [phase, selectedTheme] }
    );

    const [idx, setIdx] = useState(0);
    const [boardFen, setBoardFen] = useState("");
    const [solutionIdx, setSolutionIdx] = useState(0);
    const [feedback, setFeedback] = useState("");
    const [startedAt, setStartedAt] = useState(Date.now());
    const [hints, setHints] = useState(0);

    const puzzle = puzzles?.[idx];

    useEffect(() => {
        if (!puzzle) return;
        const b = new Chess(puzzle.fen);
        const firstUci = puzzle.solution_uci.split(" ")[0];
        try {
            b.move({ from: firstUci.slice(0, 2), to: firstUci.slice(2, 4) });
        } catch {
            try {
                b.move({ from: firstUci.slice(0, 2), to: firstUci.slice(2, 4), promotion: firstUci[4] || "q" });
            } catch { }
        }
        setBoardFen(b.fen());
        setSolutionIdx(1);
        setFeedback("");
        setStartedAt(Date.now());
        setHints(0);
    }, [idx, puzzle]);

    const record = async (correct: boolean) => {
        if (!puzzle) return;
        try {
            await invoke("record_puzzle_attempt", {
                a: {
                    puzzle_id: puzzle.puzzle_id,
                    correct,
                    hints_used: hints,
                    time_ms: Date.now() - startedAt,
                },
            });
        } catch (e) {
            console.error(e);
        }
        setTimeout(() => setIdx((i) => (i + 1) % (puzzles?.length ?? 1)), 900);
    };

    const handleMove = (from: string, to: string) => {
        if (!puzzle) return false;
        const b = new Chess(boardFen);

        let m;
        try {
            m = b.move({ from, to });
        } catch {
            m = null;
        }
        if (!m) {
            try {
                m = b.move({ from, to, promotion: "q" });
            } catch {
                return false;
            }
        }
        if (!m) return false;

        const solution = puzzle.solution_uci.split(" ");
        const expected = solution[solutionIdx];
        if (m.from + m.to + (m.promotion || "") === expected) {
            setFeedback("✓ Correct");
            const next = solutionIdx + 1;
            if (next >= solution.length) {
                record(true);
            } else {
                const reply = solution[next];
                try {
                    b.move({ from: reply.slice(0, 2), to: reply.slice(2, 4) });
                } catch {
                    try {
                        b.move({ from: reply.slice(0, 2), to: reply.slice(2, 4), promotion: reply[4] || "q" });
                    } catch { }
                }
                setBoardFen(b.fen());
                setSolutionIdx(next + 1);
            }
        } else {
            setFeedback("✗ Wrong — try again");
        }
        return true;
    };

    const hint = () => {
        setHints((h) => h + 1);
        if (!puzzle) return;
        const solution = puzzle.solution_uci.split(" ");
        const m = solution[solutionIdx];
        if (m) setFeedback(`Hint: from ${m.slice(0, 2)}`);
    };

    if (loading) {
        return (
            <div className="puzzle-trainer">
                <h3>Puzzles</h3>
                <div className="empty">Loading puzzles…</div>
            </div>
        );
    }

    if (!puzzle) {
        return (
            <div className="puzzle-trainer">
                <h3>Puzzles</h3>
                <div className="empty">
                    No puzzles due. Load them in a terminal:
                    <pre style={{ marginTop: 8, fontSize: 11 }}>winget install Facebook.Zstandard</pre>
                    <pre style={{ fontSize: 11 }}>python -m ingest.puzzle_loader --source lichess</pre>
                </div>
            </div>
        );
    }

    return (
        <div className="puzzle-trainer">
            <h3>Puzzles</h3>

            <div className="pz-header">
                <span className="pz-rating">{puzzle.rating ?? "—"}</span>
                <span className="pz-source">{puzzle.source}</span>
                {puzzle.eco_code && <span className="pz-eco">{puzzle.eco_code}</span>}
            </div>

            <div className="pz-filters">
                <select value={phase} onChange={(e) => setPhase(e.target.value)}>
                    <option value="">All phases</option>
                    <option value="Opening">Opening</option>
                    <option value="Middlegame">Middlegame</option>
                    <option value="Endgame">Endgame</option>
                </select>
                <select value={selectedTheme} onChange={(e) => setSelectedTheme(e.target.value)}>
                    <option value="">All themes</option>
                    {(themes ?? []).map(([t, count]) => (
                        <option key={t} value={t}>{t} ({count})</option>
                    ))}
                </select>
            </div>

            <Chessboard
                position={boardFen}
                onPieceDrop={handleMove}
                boardWidth={420}
                customDarkSquareStyle={{ backgroundColor: BOARD_DARK }}
                customLightSquareStyle={{ backgroundColor: BOARD_LIGHT }}
            />

            <div className="pz-themes">{puzzle.themes.join(" · ")}</div>
            <div className={`pz-feedback ${feedback.startsWith("✓") ? "ok" : "bad"}`}>
                {feedback}
            </div>
            <div className="pz-actions">
                <button onClick={hint}>Hint ({hints})</button>
                <button onClick={() => record(false)}>Give up</button>
            </div>
        </div>
    );
};
