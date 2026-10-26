import { useInvoke } from "../hooks/useInvoke";
import { LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine, ResponsiveContainer } from "recharts";

interface EvalPoint {
  ply: number;
  move_san: string;
  fen: string;
  white_eval_cp: number;
  played_by_user: boolean;
  user_loss_cp: number | null;
}

const LOSS_COLOR = (loss: number | null): string => {
  if (loss === null || loss < 50) return "#22d3ee";
  if (loss < 100) return "#eab308";
  if (loss < 300) return "#f97316";
  return "#ef4444";
};

export const EvalGraph = ({
  gameId,
  onJumpToPly,
  currentPly,
}: {
  gameId: number | null;
  onJumpToPly?: (ply: number, fen: string) => void;
  currentPly?: number;
}) => {
  const { data, loading, error } = useInvoke<EvalPoint[]>(
    "get_eval_curve",
    { gameId },
    { immediate: gameId !== null, deps: [gameId] }
  );

  if (!gameId) return <div className="eval-graph empty">Load a game to see its eval curve.</div>;
  if (loading) return <div className="eval-graph">Loading eval curve…</div>;
  if (error) return <div className="eval-graph error">{error}</div>;
  if (!data || data.length === 0)
    return <div className="eval-graph empty">Eval curve not computed yet. Accuracy analysis may still be running.</div>;

  // Cap eval for display (blowouts otherwise flatten the interesting range)
  const capped = data.map((p) => ({
    ...p,
    displayEval: Math.max(-800, Math.min(800, p.white_eval_cp)),
  }));

  return (
    <div className="eval-graph">
      <div className="eval-graph-header">
        <h4>Game Evaluation</h4>
        <div className="eval-legend">
          <span><span className="dot" style={{ background: "#22d3ee" }} />Good</span>
          <span><span className="dot" style={{ background: "#eab308" }} />Inaccuracy</span>
          <span><span className="dot" style={{ background: "#f97316" }} />Mistake</span>
          <span><span className="dot" style={{ background: "#ef4444" }} />Blunder</span>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={160}>
        <LineChart
          data={capped}
          onClick={(e: any) => {
            if (e?.activePayload?.[0]?.payload && onJumpToPly) {
              const p = e.activePayload[0].payload;
              onJumpToPly(p.ply, p.fen);
            }
          }}
          style={{ cursor: onJumpToPly ? "pointer" : "default" }}
        >
          <XAxis
            dataKey="ply"
            tick={{ fontSize: 10, fill: "#6b7280" }}
            tickFormatter={(v) => String(Math.ceil(v / 2))}
          />
          <YAxis
            domain={[-800, 800]}
            tick={{ fontSize: 10, fill: "#6b7280" }}
            tickFormatter={(v) => (v / 100).toFixed(0)}
          />
          <ReferenceLine y={0} stroke="#52525b" strokeDasharray="2 2" />
          {currentPly !== undefined && (
            <ReferenceLine x={currentPly} stroke="#22d3ee" strokeDasharray="4 4" />
          )}
          <Tooltip
            contentStyle={{ background: "#17171c", border: "1px solid #333", fontSize: 11 }}
            formatter={(value: any, _name, payload: any) => {
              const p = payload.payload as EvalPoint;
              const evalStr = (p.white_eval_cp / 100).toFixed(2);
              const lossStr = p.user_loss_cp !== null ? ` (loss: ${p.user_loss_cp})` : "";
              return [`${evalStr}${lossStr}`, p.move_san];
            }}
            labelFormatter={(label) => `Move ${Math.ceil(Number(label) / 2)}`}
          />
          <Line
            type="monotone"
            dataKey="displayEval"
            stroke="#22d3ee"
            strokeWidth={2}
            dot={(props: any) => {
              const p = props.payload as EvalPoint;
              if (!p.played_by_user) {
                return <circle key={`d-${p.ply}`} cx={props.cx} cy={props.cy} r={0} />;
              }
              return (
                <circle
                  key={`d-${p.ply}`}
                  cx={props.cx}
                  cy={props.cy}
                  r={4}
                  fill={LOSS_COLOR(p.user_loss_cp)}
                  stroke="#0F0F11"
                  strokeWidth={1}
                />
              );
            }}
          />
        </LineChart>
      </ResponsiveContainer>

      <div className="blunder-list">
        <h5>Blunders ({data.filter((p) => p.played_by_user && (p.user_loss_cp ?? 0) >= 300).length})</h5>
        {data
          .filter((p) => p.played_by_user && (p.user_loss_cp ?? 0) >= 100)
          .map((p) => (
            <button
              key={p.ply}
              className={`blunder-row ${(p.user_loss_cp ?? 0) >= 300 ? "blunder" : "mistake"}`}
              onClick={() => onJumpToPly?.(p.ply, p.fen)}
            >
              <span className="blunder-move">{p.move_san}</span>
              <span className="blunder-loss">-{(p.user_loss_cp ?? 0) / 100} pawns</span>
              <span className="blunder-movenum">Move {Math.ceil(p.ply / 2)}</span>
            </button>
          ))}
      </div>
    </div>
  );
};
