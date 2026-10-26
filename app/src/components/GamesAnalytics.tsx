import { useState } from "react";
import { useInvoke } from "../hooks/useInvoke";

interface GambitRow {
  gambit_id: string;
  gambit_name: string;
  side: string;
  soundness?: string;
  games: number;
  wins: number;
  losses: number;
  draws: number;
  score: number;
}
interface EcoRow {
  eco_code: string;
  opening_name: string;
  color: string;
  games: number;
  score: number;
}
interface AccuracyRow {
  source: string;
  ref_id: number;
  participant: string;
  color: string;
  accuracy: number;
  acpl: number;
  moves_analyzed: number;
}
interface Summary {
  participant: string;
  participant_type: string;
  source: string;
  games: number;
  avg_accuracy: number;
  avg_acpl: number;
  best_accuracy: number;
  best_acpl: number;
}

type SubTab = "overview" | "gambits" | "eco" | "accuracy";

export const GamesAnalytics = ({
  participant,
  source,
}: {
  participant: string;
  source: "personal" | "eval";
}) => {
  const [sub, setSub] = useState<SubTab>("overview");

  const { data: summaries } = useInvoke<Summary[]>("get_participant_summaries");
  const { data: gambits } = useInvoke<GambitRow[]>(
    "get_participant_gambit_stats",
    { participant, source, minGames: 1 },
    { deps: [participant, source] }
  );
  const { data: eco } = useInvoke<EcoRow[]>(
    "get_participant_eco_stats",
    { participant, color: null, minGames: 3 },
    { deps: [participant] }
  );
  const { data: accuracy } = useInvoke<AccuracyRow[]>(
    "get_recent_accuracy",
    { participant, source, limit: 30 },
    { deps: [participant, source] }
  );

  const my = summaries?.find((s) => s.participant === participant && s.source === source);

  return (
    <div className="games-analytics">
      <div className="ga-header">
        <h2>
          {source === "eval" ? "Engine" : "Personal"} Analytics — {participant}
        </h2>
        {my && (
          <span className="ga-summary">
            {my.games} games · {my.avg_accuracy.toFixed(1)}% avg ·{" "}
            {my.avg_acpl.toFixed(1)} ACPL
          </span>
        )}
      </div>

      <div className="ga-subtabs">
        {(["overview", "gambits", "eco", "accuracy"] as const).map((t) => (
          <button
            key={t}
            className={`subtab ${sub === t ? "active" : ""}`}
            onClick={() => setSub(t)}
          >
            {t}
          </button>
        ))}
      </div>

      {sub === "overview" && (
        <div className="ga-overview">
          <div className="ga-metric">
            <span className="ga-metric-value">{gambits?.length ?? 0}</span>
            <span className="ga-metric-label">Distinct gambits</span>
          </div>
          <div className="ga-metric">
            <span className="ga-metric-value">
              {gambits?.filter((g) => g.score > 0.5).length ?? 0}
            </span>
            <span className="ga-metric-label">Positive-score gambits</span>
          </div>
          <div className="ga-metric">
            <span className="ga-metric-value">
              {accuracy?.[0]?.accuracy.toFixed(1) ?? "—"}%
            </span>
            <span className="ga-metric-label">Latest accuracy</span>
          </div>
        </div>
      )}

      {sub === "gambits" && (
        <div className="ga-list">
          {(gambits ?? []).map((g) => (
            <div key={g.gambit_id} className="ga-row">
              <span className="ga-name">{g.gambit_name}</span>
              <span>{g.games} games</span>
              <span>
                W{g.wins} L{g.losses} D{g.draws}
              </span>
              <span className={g.score > 0.55 ? "hot" : g.score < 0.45 ? "cold" : ""}>
                {(g.score * 100).toFixed(1)}%
              </span>
            </div>
          ))}
        </div>
      )}

      {sub === "eco" && (
        <div className="ga-list">
          {(eco ?? []).map((e) => (
            <div key={`${e.eco_code}_${e.color}`} className="ga-row">
              <span className="ga-name">
                {e.eco_code} {e.opening_name}
              </span>
              <span>{e.color}</span>
              <span>{e.games} games</span>
              <span className={e.score > 0.55 ? "hot" : e.score < 0.45 ? "cold" : ""}>
                {(e.score * 100).toFixed(1)}%
              </span>
            </div>
          ))}
        </div>
      )}

      {sub === "accuracy" && (
        <div className="ga-list">
          {(accuracy ?? []).map((a) => (
            <div key={`${a.source}_${a.ref_id}`} className="ga-row">
              <span className="ga-name">{a.source} #{a.ref_id}</span>
              <span>{a.accuracy.toFixed(1)}%</span>
              <span>{a.acpl.toFixed(1)} ACPL</span>
              <span>{a.moves_analyzed} moves</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
