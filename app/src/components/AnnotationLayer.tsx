// SVG overlay drawn on top of the chessboard. Handles:
// - Right-click drag = draw arrow (color cycle: green/red/blue/yellow)
// - Left-click square = highlight toggle
// - Ctrl+click square = clear highlight
//
// Arrow/highlight data lives in parent state and syncs to DB on save.

import { useState, useRef, useCallback } from "react";

export interface Arrow { from: string; to: string; color: string }
export interface Highlight { square: string; color: string }

const COLORS = ["#22c55e", "#ef4444", "#3b82f6", "#eab308"];

const fileToX = (f: string) => "abcdefgh".indexOf(f);
const rankToY = (r: string) => 8 - parseInt(r, 10);

const squareToXY = (sq: string): [number, number] => {
  const f = sq[0], r = sq[1];
  return [fileToX(f), rankToY(r)];
};

export const AnnotationLayer = ({
  boardSize,
  arrows,
  highlights,
  onAddArrow,
  onAddHighlight,
  onClearSquare,
  interactive,
}: {
  boardSize: number;
  arrows: Arrow[];
  highlights: Highlight[];
  onAddArrow: (a: Arrow) => void;
  onAddHighlight: (h: Highlight) => void;
  onClearSquare: (sq: string) => void;
  interactive: boolean;
}) => {
  const [dragStart, setDragStart] = useState<string | null>(null);
  const [dragEnd, setDragEnd] = useState<{ x: number; y: number } | null>(null);
  const colorIdx = useRef(0);
  const squareSize = boardSize / 8;

  const squareFromPoint = useCallback((x: number, y: number): string | null => {
    const f = Math.floor(x / squareSize);
    const r = Math.floor(y / squareSize);
    if (f < 0 || f > 7 || r < 0 || r > 7) return null;
    return "abcdefgh"[f] + (8 - r);
  }, [squareSize]);

  const handleMouseDown = (e: React.MouseEvent) => {
    if (!interactive) return;
    if (e.button === 2) {
      const rect = e.currentTarget.getBoundingClientRect();
      const sq = squareFromPoint(e.clientX - rect.left, e.clientY - rect.top);
      if (sq) setDragStart(sq);
    } else if (e.button === 0) {
      const rect = e.currentTarget.getBoundingClientRect();
      const sq = squareFromPoint(e.clientX - rect.left, e.clientY - rect.top);
      if (!sq) return;
      if (e.ctrlKey || e.metaKey) {
        onClearSquare(sq);
      } else {
        onAddHighlight({ square: sq, color: COLORS[colorIdx.current] });
        colorIdx.current = (colorIdx.current + 1) % COLORS.length;
      }
    }
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!interactive || !dragStart) return;
    const rect = e.currentTarget.getBoundingClientRect();
    setDragEnd({ x: e.clientX - rect.left, y: e.clientY - rect.top });
  };

  const handleMouseUp = (e: React.MouseEvent) => {
    if (!interactive || !dragStart) return;
    if (e.button === 2) {
      const rect = e.currentTarget.getBoundingClientRect();
      const endSq = squareFromPoint(e.clientX - rect.left, e.clientY - rect.top);
      if (endSq && endSq !== dragStart) {
        onAddArrow({ from: dragStart, to: endSq, color: COLORS[colorIdx.current] });
        colorIdx.current = (colorIdx.current + 1) % COLORS.length;
      }
    }
    setDragStart(null);
    setDragEnd(null);
  };

  return (
    <svg
      width={boardSize}
      height={boardSize}
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        pointerEvents: interactive ? "auto" : "none",
        cursor: interactive ? "crosshair" : "default",
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={(e) => e.preventDefault()}
    >
      {highlights.map((h, i) => {
        const [x, y] = squareToXY(h.square);
        return (
          <rect
            key={`h-${i}`}
            x={x * squareSize}
            y={y * squareSize}
            width={squareSize}
            height={squareSize}
            fill={h.color}
            opacity={0.35}
          />
        );
      })}
      {arrows.map((a, i) => {
        const [x1, y1] = squareToXY(a.from);
        const [x2, y2] = squareToXY(a.to);
        const cx1 = x1 * squareSize + squareSize / 2;
        const cy1 = y1 * squareSize + squareSize / 2;
        const cx2 = x2 * squareSize + squareSize / 2;
        const cy2 = y2 * squareSize + squareSize / 2;
        const angle = Math.atan2(cy2 - cy1, cx2 - cx1);
        const headLen = 12;
        const bx = cx2 - Math.cos(angle) * headLen;
        const by = cy2 - Math.sin(angle) * headLen;
        return (
          <g key={`a-${i}`}>
            <line x1={cx1} y1={cy1} x2={bx} y2={by} stroke={a.color} strokeWidth={6} strokeLinecap="round" opacity={0.85} />
            <polygon
              points={`${cx2},${cy2} ${bx - Math.sin(angle) * headLen * 0.6},${by + Math.cos(angle) * headLen * 0.6} ${bx + Math.sin(angle) * headLen * 0.6},${by - Math.cos(angle) * headLen * 0.6}`}
              fill={a.color}
              opacity={0.85}
            />
          </g>
        );
      })}
      {dragStart && dragEnd && (
        <line
          x1={squareToXY(dragStart)[0] * squareSize + squareSize / 2}
          y1={squareToXY(dragStart)[1] * squareSize + squareSize / 2}
          x2={dragEnd.x}
          y2={dragEnd.y}
          stroke={COLORS[colorIdx.current]}
          strokeWidth={4}
          strokeDasharray="8,4"
          opacity={0.6}
        />
      )}
    </svg>
  );
};
