import { useInvoke } from "../hooks/useInvoke";

interface LegendRow {
  legend: string;
  games: number;
  score: number;
}
interface OpeningNode {
  mv: string;
  fen: string;
  games: number;
  white_score: number;
  by_legend: LegendRow[];
}

export const OpeningExplorer = ({ currentFen }: { currentFen: string }) => {
  const { data: nodes, loading, error } = useInvoke<OpeningNode[]>(
    "explore_opening",
    { fen: currentFen, minGames: 20 },
    { deps: [currentFen] }
  );

  return (
    <div className="opening-explorer">
      <h3>Opening Explorer</h3>
      {loading && <div className="loading">Scanning GM database…</div>}
      {error && <div className="error">{error}</div>}
      {nodes?.map((n) => (
        <div key={n.mv} className="opening-row">
          <span className="mv">{n.mv}</span>
          <span className="games">{n.games.toLocaleString()}</span>
          <span
            className={`score ${
              n.white_score > 0.55 ? "hot" : n.white_score < 0.45 ? "cold" : ""
            }`}
          >
            {(n.white_score * 100).toFixed(1)}%
          </span>
          <div className="legend-breakdown">
            {n.by_legend.map((l) => (
              <span key={l.legend} className="legend-chip" title={`${l.games} games`}>
                {l.legend.slice(0, 3).toUpperCase()} {(l.score * 100).toFixed(0)}
              </span>
            ))}
          </div>
        </div>
      ))}
      {nodes?.length === 0 && !loading && (
        <div className="empty">No games match this position.</div>
      )}
    </div>
  );
};
