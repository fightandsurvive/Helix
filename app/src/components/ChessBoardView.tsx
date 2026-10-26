import { Chessboard } from "react-chessboard";
import { Chess } from "chess.js";
import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

interface Props {
    fen: string;
    setFen: (f: string) => void;
    gameId?: number | null;
    plyNumber?: number;
}

interface Highlight {
    square: string;
    color: string;
}

export const ChessBoardView = ({
    fen,
    setFen,
    gameId,
    plyNumber = 0,
}: Props) => {
    const [lastMove, setLastMove] = useState<[string, string] | null>(null);
    const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
    const [highlights, setHighlights] = useState<Highlight[]>([]);

    // Load saved highlights when position changes
    useEffect(() => {
        if (!gameId) {
            setHighlights([]);
            return;
        }
        invoke<any[]>("get_annotations", { gameId })
            .then((rows) => {
                const match = rows.find((r) => r.ply_number === plyNumber);
                setHighlights(match?.highlights ?? []);
            })
            .catch(() => setHighlights([]));
    }, [gameId, plyNumber]);

    const persistHighlights = (hs: Highlight[]) => {
        if (!gameId) return;
        invoke("save_annotation", {
            a: {
                game_id: gameId,
                ply_number: plyNumber,
                arrows: [],
                highlights: hs,
                comment: null,
            },
        }).catch(() => { });
    };

    const tryMove = (from: string, to: string): boolean => {
        const g = new Chess(fen);
        // Attempt without promotion first — chess.js rejects promotion
        // param on non-promotion moves, which is the bug we had.
        try {
            const m = g.move({ from, to });
            if (m) {
                setFen(g.fen());
                setLastMove([from, to]);
                setSelectedSquare(null);
                return true;
            }
        } catch {
            /* fall through to promotion attempt */
        }
        // Attempt with queen promotion (only needed when target rank requires)
        try {
            const m = g.move({ from, to, promotion: "q" });
            if (m) {
                setFen(g.fen());
                setLastMove([from, to]);
                setSelectedSquare(null);
                return true;
            }
        } catch {
            /* illegal move */
        }
        return false;
    };

    const onSquareClick = (sq: string) => {
        if (selectedSquare) {
            if (tryMove(selectedSquare, sq)) return;
            setSelectedSquare(null);
        }
        const g = new Chess(fen);
        const piece = g.get(sq as any);
        if (piece && (piece.color === "w") === (g.turn() === "w")) {
            setSelectedSquare(sq);
        }
    };

    const onSquareRightClick = (sq: string) => {
        const idx = highlights.findIndex((h) => h.square === sq);
        const next =
            idx >= 0
                ? highlights.filter((_, i) => i !== idx)
                : [...highlights, { square: sq, color: "#22c55e" }];
        setHighlights(next);
        persistHighlights(next);
    };

    const squareStyles: Record<string, React.CSSProperties> = {};
    if (lastMove) {
        squareStyles[lastMove[0]] = { backgroundColor: "rgba(34,211,238,0.20)" };
        squareStyles[lastMove[1]] = { backgroundColor: "rgba(34,211,238,0.35)" };
    }
    if (selectedSquare) {
        squareStyles[selectedSquare] = { backgroundColor: "rgba(34,211,238,0.50)" };
    }
    highlights.forEach((h) => {
        squareStyles[h.square] = {
            ...squareStyles[h.square],
            backgroundColor: h.color + "66",
        };
    });

    return (
        <div className="board-wrapper" style={{ position: "relative" }}>
            <Chessboard
                position={fen}
                boardWidth={640}
                onPieceDrop={(from, to) => tryMove(from, to)}
                onSquareClick={(sq) => onSquareClick(sq)}
                onSquareRightClick={(sq) => onSquareRightClick(sq)}
                customDarkSquareStyle={{ backgroundColor: "#0a0a0c" }}
                customLightSquareStyle={{ backgroundColor: "rgba(220,235,245,0.85)" }}
                customBoardStyle={{
                    borderRadius: 6,
                    boxShadow:
                        "0 0 60px rgba(34,211,238,0.15), inset 0 0 30px rgba(0,0,0,0.4)",
                }}
                customSquareStyles={squareStyles}
            />
        </div>
    );
};
