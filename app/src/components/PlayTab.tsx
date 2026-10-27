import { useState, useEffect, useRef } from "react";
import { Chess } from "chess.js";
import { Chessboard } from "react-chessboard";
import { invoke } from "@tauri-apps/api/core";
import { BOARD_DARK, BOARD_LIGHT } from "./ChessBoardView";

type GameState = "idle" | "playing" | "finished";

const SKILL_LABELS: Record<number, string> = {
    1: "Beginner (~800)", 3: "Casual (~1200)", 5: "Club (~1500)",
    8: "Strong club (~1800)", 12: "Expert (~2100)", 16: "Master (~2400)",
    20: "Full Stockfish",
};

export const PlayTab = ({
    profileId,
    onGameSaved,
}: {
    profileId: number;
    onGameSaved: (gameId: number) => void;
}) => {
    const [state, setState] = useState<GameState>("idle");
    const [game] = useState(new Chess());
    const [fen, setFen] = useState(game.fen());
    const [userColor, setUserColor] = useState<"white" | "black">("white");
    const [skill, setSkill] = useState(8);
    const [timeMin, setTimeMin] = useState(5);
    const [incSec, setIncSec] = useState(0);
    const [whiteClock, setWhiteClock] = useState(5 * 60 * 1000);
    const [blackClock, setBlackClock] = useState(5 * 60 * 1000);
    const [status, setStatus] = useState("");
    const [thinking, setThinking] = useState(false);
    const [savedGameId, setSavedGameId] = useState<number | null>(null);
    const clockRef = useRef<number | null>(null);

    useEffect(() => {
        if (state !== "playing") return;
        clockRef.current = window.setInterval(() => {
            const turn = game.turn();
            if (turn === "w") setWhiteClock((c) => Math.max(0, c - 100));
            else setBlackClock((c) => Math.max(0, c - 100));
        }, 100);
        return () => {
            if (clockRef.current) clearInterval(clockRef.current);
        };
    }, [state, game]);

    const startGame = () => {
        game.reset();
        setFen(game.fen());
        setWhiteClock(timeMin * 60 * 1000);
        setBlackClock(timeMin * 60 * 1000);
        setStatus("");
        setSavedGameId(null);
        setState("playing");
        if (userColor === "black") {
            requestSfMove();
        }
    };

    const requestSfMove = async () => {
        setThinking(true);
        try {
            const sfPath = "stockfish";
            const movetime = Math.min(2000, Math.max(200, Math.floor((timeMin * 60 * 1000) / 30)));
            const resp = await invoke<any>("sf_play_move", {
                req: {
                    fen: game.fen(),
                    moves: game.history(),
                    skill_level: skill,
                    movetime_ms: movetime,
                },
                sfPath,
            });
            const uci = resp.best_move_uci;
            if (uci && uci.length >= 4) {
                try {
                    game.move({ from: uci.slice(0, 2), to: uci.slice(2, 4) });
                } catch {
                    game.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: "q" });
                }
                setFen(game.fen());
                const turn = game.turn();
                if (turn === "w") setWhiteClock((c) => c + incSec * 1000);
                else setBlackClock((c) => c + incSec * 1000);
                checkGameEnd();
            }
        } catch (e) {
            setStatus(`SF error: ${e}`);
        } finally {
            setThinking(false);
        }
    };

    const handleUserMove = (from: string, to: string) => {
        if (state !== "playing" || thinking) return false;
        if (game.turn() !== userColor[0]) return false;

        let m;
        try {
            m = game.move({ from, to });
        } catch {
            m = null;
        }
        if (!m) {
            try {
                m = game.move({ from, to, promotion: "q" });
            } catch {
                return false;
            }
        }
        if (!m) return false;

        setFen(game.fen());
        const turn = game.turn();
        if (turn === "w") setWhiteClock((c) => c + incSec * 1000);
        else setBlackClock((c) => c + incSec * 1000);
        if (!checkGameEnd()) {
            requestSfMove();
        }
        return true;
    };

    const checkGameEnd = (): boolean => {
        if (game.isGameOver()) {
            setState("finished");
            const r = game.isCheckmate()
                ? (game.turn() === "w" ? "0-1" : "1-0")
                : "1/2-1/2";
            const userWon =
                (r === "1-0" && userColor === "white") ||
                (r === "0-1" && userColor === "black");
            setStatus(game.isCheckmate() ? (userWon ? "You win!" : "You lose.") : "Draw.");
            saveGame(r);
            return true;
        }
        return false;
    };

    const saveGame = async (result: string) => {
        try {
            const id = await invoke<number>("save_played_game", {
                req: {
                    profile_id: profileId,
                    pgn: game.pgn(),
                    white_player: userColor === "white" ? "You" : `Stockfish (skill ${skill})`,
                    black_player: userColor === "black" ? "You" : `Stockfish (skill ${skill})`,
                    user_color: userColor,
                    result:
                        result === "1-0"
                            ? (userColor === "white" ? "win" : "loss")
                            : result === "0-1"
                                ? (userColor === "black" ? "win" : "loss")
                                : "draw",
                    user_rating: null,
                    opponent_rating: null,
                    time_control: `${timeMin}+${incSec}`,
                },
            });
            setSavedGameId(id);
            onGameSaved(id);
        } catch (e) {
            console.error("save failed:", e);
        }
    };

    const resign = () => {
        const r = userColor === "white" ? "0-1" : "1-0";
        setState("finished");
        setStatus("You resigned.");
        saveGame(r);
    };

    const formatClock = (ms: number) => {
        const total = Math.max(0, Math.floor(ms / 1000));
        const m = Math.floor(total / 60);
        const s = total % 60;
        return `${m}:${s.toString().padStart(2, "0")}`;
    };

    return (
        <div className="play-tab">
            {state === "idle" && (
                <div className="play-config">
                    <h3>Play vs Stockfish</h3>
                    <div className="play-config-row">
                        <label>Your color</label>
                        <select value={userColor} onChange={(e) => setUserColor(e.target.value as "white" | "black")}>
                            <option value="white">White</option>
                            <option value="black">Black</option>
                        </select>
                    </div>
                    <div className="play-config-row">
                        <label>Opponent strength</label>
                        <select value={skill} onChange={(e) => setSkill(Number(e.target.value))}>
                            {[1, 3, 5, 8, 12, 16, 20].map((s) => (
                                <option key={s} value={s}>{SKILL_LABELS[s]}</option>
                            ))}
                        </select>
                    </div>
                    <div className="play-config-row">
                        <label>Time control</label>
                        <div className="tc-inputs">
                            <input type="number" min={1} max={60} value={timeMin}
                                onChange={(e) => setTimeMin(Number(e.target.value))} /> min
                            <input type="number" min={0} max={30} value={incSec}
                                onChange={(e) => setIncSec(Number(e.target.value))} /> sec increment
                        </div>
                    </div>
                    <button className="play-start" onClick={startGame}>Start Game</button>
                </div>
            )}

            {state !== "idle" && (
                <>
                    <div className="play-clock-row">
                        <div className={`play-clock ${game.turn() === "w" ? "active" : ""}`}>
                            {formatClock(userColor === "white" ? whiteClock : blackClock)}
                        </div>
                        <div className={`play-clock ${game.turn() === "b" ? "active" : ""}`}>
                            {formatClock(userColor === "white" ? blackClock : whiteClock)}
                        </div>
                    </div>
                    <div style={{ position: "relative" }}>
                        <Chessboard
                            position={fen}
                            boardWidth={560}
                            onPieceDrop={handleUserMove}
                            boardOrientation={userColor}
                            arePiecesDraggable={state === "playing" && !thinking}
                            customDarkSquareStyle={{ backgroundColor: BOARD_DARK }}
                            customLightSquareStyle={{ backgroundColor: BOARD_LIGHT }}
                        />
                        {thinking && <div className="play-thinking">Stockfish thinking…</div>}
                    </div>
                    {status && <div className={`play-status ${savedGameId ? "saved" : ""}`}>{status}</div>}
                    <div className="play-actions">
                        {state === "playing" && (
                            <button className="play-resign" onClick={resign}>Resign</button>
                        )}
                        {state === "finished" && (
                            <>
                                <button className="play-start" onClick={() => setState("idle")}>New Game</button>
                                {savedGameId && (
                                    <span className="play-saved-hint">
                                        Game saved — accuracy will be computed shortly
                                    </span>
                                )}
                            </>
                        )}
                    </div>
                </>
            )}
        </div>
    );
};
