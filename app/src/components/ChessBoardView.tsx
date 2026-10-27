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

// Shared board colors — used everywhere a Chessboard is rendered.
// Steel blue for dark, soft cream for light. Good contrast for both
// white and black pieces; classic chess aesthetic without being dull.
export const BOARD_DARK = "#4a6b8a";
export const BOARD_LIGHT = "#d6dce5";

export const ChessBoardView = ({
    fen,
    setFen,
    gameId,
    plyNumber = 0,
}: Props) => {
    const [lastMove, setLastMove] = useState<[string, string] | null>(null);
    const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
    const [highlights, setHighlights] = useState<Highlight[]>([]);

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
        // Try without promotion first — chess.js rejects promotion param on
        // non-promotion moves, which was the drag-fail bug.
        try {
            const m = g.move({ from, to });
            if (m) {
                setFen(g.fen());
                setLastMove([from, to]);
                setSelectedSquare(null);
                return true;
            }
        } catch { }
        // Fall back to queen promotion (only fires when pawn reaches back rank)
        try {
            const m = g.move({ from, to, promotion: "q" });
            if (m) {
                setFen(g.fen());
                setLastMove([from, to]);
                setSelectedSquare(null);
                return true;
            }
        } catch { }
        return false;
    };

    const onDrop = (from: string, to: string): boolean => {
        console.debug("[ChessBoardView] onPieceDrop:", from, "→", to);
        return tryMove(from, to);
    };

    const onDragBegin = () => {
        // CRITICAL: clear external click-selection on drag start so it does
        // not fight react-chessboard's internal drag state.
        setSelectedSquare(null);
    };

    const onClick = (sq: string) => {
        if (selectedSquare) {
            if (tryMove(selectedSquare, sq)) return;
            setSelectedSquare(null);
            return;
        }
        const g = new Chess(fen);
        const piece = g.get(sq as any);
        if (piece && (piece.color === "w") === (g.turn() === "w")) {
            setSelectedSquare(sq);
        }
    };

    const onRightClick = (sq: string) => {
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
        squareStyles[lastMove[0]] = { backgroundColor: "rgba(34,211,238,0.35)" };
        squareStyles[lastMove[1]] = { backgroundColor: "rgba(34,211,238,0.55)" };
    }
    if (selectedSquare) {
        squareStyles[selectedSquare] = { backgroundColor: "rgba(34,211,238,0.65)" };
    }
    highlights.forEach((h) => {
        squareStyles[h.square] = {
            ...squareStyles[h.square],
            backgroundColor: h.color + "88",
        };
    });

    return (
        <div className="board-wrapper" style={{ position: "relative" }}>
            <Chessboard
                position={fen}
                boardWidth={640}
                arePiecesDraggable={true}
                onPieceDrop={onDrop}
                onPieceDragBegin={onDragBegin}
                onSquareClick={onClick}
                onSquareRightClick={onRightClick}
                customDarkSquareStyle={{ backgroundColor: BOARD_DARK }}
                customLightSquareStyle={{ backgroundColor: BOARD_LIGHT }}
                customBoardStyle={{
                    borderRadius: 6,
                    boxShadow:
                        "0 0 60px rgba(34,211,238,0.20), inset 0 0 30px rgba(0,0,0,0.3)",
                }}
                customSquareStyles={squareStyles}
            />
        </div>
    );
};
