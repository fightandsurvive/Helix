import { Chessboard } from "react-chessboard";
import { Chess } from "chess.js";
import { useEffect, useRef, useState } from "react";
import { AnnotationLayer, Arrow, Highlight } from "./AnnotationLayer";

interface Props {
  fen: string;
  setFen: (f: string) => void;
  gameId?: number | null;
  plyNumber?: number;
  interactiveAnnotations?: boolean;
}

export const ChessBoardView = ({
  fen,
  setFen,
  gameId,
  plyNumber = 0,
  interactiveAnnotations = false,
}: Props) => {
  const [lastMove, setLastMove] = useState<[string, string] | null>(null);
  const [arrows, setArrows] = useState<Arrow[]>([]);
  const [highlights, setHighlights] = useState<Highlight[]>([]);
  const boardRef = useRef<HTMLDivElement>(null);
  const [boardSize, setBoardSize] = useState(640);

  // Load annotations when position changes
  useEffect(() => {
    if (!gameId) {
      setArrows([]);
      setHighlights([]);
      return;
    }
    import("@tauri-apps/api/core").then((m) =>
      m.invoke<any[]>("get_annotations", { gameId }).then((rows) => {
        const match = rows.find((r) => r.ply_number === plyNumber);
        setArrows(match?.arrows ?? []);
        setHighlights(match?.highlights ?? []);
      })
    );
  }, [gameId, plyNumber]);

  // Measure board for annotation overlay
  useEffect(() => {
    const measure = () => {
      if (boardRef.current) setBoardSize(boardRef.current.offsetWidth);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const persistAnnotations = (newArrows: Arrow[], newHighlights: Highlight[]) => {
    if (!gameId) return;
    import("@tauri-apps/api/core").then((m) =>
      m.invoke("save_annotation", {
        a: {
          game_id: gameId,
          ply_number: plyNumber,
          arrows: newArrows,
          highlights: newHighlights,
          comment: null,
        },
      })
    );
  };

  return (
    <div className="board-wrapper" ref={boardRef} style={{ position: "relative" }}>
      <Chessboard
        position={fen}
        boardWidth={640}
        onPieceDrop={(from, to, piece) => {
          const g = new Chess(fen);
          try {
            const m = g.move({
              from,
              to,
              promotion: piece[1]?.toLowerCase() === "p" ? "q" : undefined,
            });
            if (!m) return false;
            setLastMove([from, to]);
            setFen(g.fen());
            return true;
          } catch {
            return false;
          }
        }}
        customDarkSquareStyle={{ backgroundColor: "#0a0a0c" }}
        customLightSquareStyle={{ backgroundColor: "rgba(220,235,245,0.85)" }}
        customBoardStyle={{
          borderRadius: 6,
          boxShadow: "0 0 60px rgba(34,211,238,0.15), inset 0 0 30px rgba(0,0,0,0.4)",
        }}
        customSquareStyles={
          lastMove
            ? {
                [lastMove[0]]: { backgroundColor: "rgba(34,211,238,0.2)" },
                [lastMove[1]]: { backgroundColor: "rgba(34,211,238,0.35)" },
              }
            : {}
        }
      />
      <AnnotationLayer
        boardSize={boardSize}
        arrows={arrows}
        highlights={highlights}
        interactive={interactiveAnnotations && !!gameId}
        onAddArrow={(a) => {
          const next = [...arrows, a];
          setArrows(next);
          persistAnnotations(next, highlights);
        }}
        onAddHighlight={(h) => {
          const next = [...highlights, h];
          setHighlights(next);
          persistAnnotations(arrows, next);
        }}
        onClearSquare={(sq) => {
          const next = highlights.filter((h) => h.square !== sq);
          setHighlights(next);
          persistAnnotations(arrows, next);
        }}
      />
    </div>
  );
};
