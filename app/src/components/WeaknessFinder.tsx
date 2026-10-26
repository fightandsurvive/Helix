import { useEffect, useState } from "react";
import { useInvoke } from "../hooks/useInvoke";
import { invoke } from "@tauri-apps/api/core";
import { motion, AnimatePresence } from "framer-motion";

interface Insight {
  insight_id: number;
  category: string;
  severity: "critical" | "high" | "medium" | "low";
  title: string;
  description: string;
  metric_name: string;
  metric_value: number;
  baseline_value: number | null;
  sample_size: number;
  confidence: number;
  impact_score: number;
  action_type: string;
  action_label: string;
  action_payload: Record<string, unknown> | null;
  evidence: { rows?: { label: string; value: number; count?: number }[]; baseline?: number } | null;
  computed_at: string;
}

const SEVERITY_COLOR = {
  critical: "#f87171",
  high: "#fb923c",
  medium: "#fbbf24",
  low: "#86efac",
};

export const WeaknessFinder = ({
  profileId,
  onAction,
}: {
  profileId: number;
  onAction?: (actionType: string, payload: Record<string, unknown> | null) => void;
}) => {
  const [minConfidence, setMinConfidence] = useState(0.0);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const { data: insights, loading, error, refetch } = useInvoke<Insight[]>(
    "get_weakness_insights",
    { profileId, minConfidence, categoryFilter: null },
    { deps: [profileId, minConfidence] }
  );

  const { data: hasInsights } = useInvoke<boolean>(
    "has_weakness_insights",
    { profileId },
    { deps: [profileId] }
  );

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await invoke("refresh_weakness_insights", { profileId });
      await refetch();
    } catch (e) {
      console.error("refresh failed:", e);
    } finally {
      setRefreshing(false);
    }
  };

  if (!hasInsights && !loading) {
    return (
      <div className="weakness-finder empty">
        <h3>Weakness Finder</h3>
        <p>
          Analyze your games to identify recurring patterns. This requires
          at least 20 imported games with accuracy data.
        </p>
        <button className="wf-refresh-btn" onClick={handleRefresh} disabled={refreshing}>
          {refreshing ? "Analyzing…" : "Run Analysis"}
        </button>
      </div>
    );
  }

  if (loading) {
    return <div className="weakness-finder"><h3>Weakness Finder</h3><div className="wf-loading">Loading…</div></div>;
  }

  if (error) {
    return <div className="weakness-finder"><h3>Weakness Finder</h3><div className="wf-error">{error}</div></div>;
  }

  const items = insights ?? [];
  const top3 = items.slice(0, 3);

  return (
    <div className="weakness-finder">
      <div className="wf-header">
        <h3>Weakness Finder</h3>
        <div className="wf-controls">
          <label>
            Min confidence:
            <select
              value={minConfidence}
              onChange={(e) => setMinConfidence(parseFloat(e.target.value))}
            >
              <option value="0">Show all</option>
              <option value="0.7">70%+</option>
              <option value="0.85">85%+</option>
              <option value="0.95">95%+</option>
            </select>
          </label>
          <button
            className="wf-refresh-btn"
            onClick={handleRefresh}
            disabled={refreshing}
            title="Recompute insights from your games"
          >
            {refreshing ? "…" : "↻ Refresh"}
          </button>
        </div>
      </div>

      {items.length === 0 && (
        <div className="wf-empty">
          No significant weaknesses detected. {minConfidence > 0
            ? "Try lowering the confidence threshold."
            : "Play more games to build a stronger signal."}
        </div>
      )}

      {top3.length > 0 && (
        <div className="wf-summary">
          <h4>Top 3 focus areas</h4>
          <ol>
            {top3.map((ins) => (
              <li key={ins.insight_id}>
                <span style={{ color: SEVERITY_COLOR[ins.severity] }}>●</span>{" "}
                {ins.title}{" "}
                <span className="wf-impact">
                  (impact: {Math.round(ins.impact_score * 100)}%)
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="wf-list">
        <AnimatePresence>
          {items.map((ins) => (
            <motion.div
              key={ins.insight_id}
              className={`wf-card severity-${ins.severity}`}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              style={{ borderLeftColor: SEVERITY_COLOR[ins.severity] }}
            >
              <button
                className="wf-card-header"
                onClick={() =>
                  setExpandedId(expandedId === ins.insight_id ? null : ins.insight_id)
                }
              >
                <span
                  className="wf-severity"
                  style={{ color: SEVERITY_COLOR[ins.severity] }}
                >
                  {ins.severity.toUpperCase()}
                </span>
                <span className="wf-title">{ins.title}</span>
                <span className="wf-confidence">
                  {Math.round(ins.confidence * 100)}% conf
                </span>
              </button>

              <div className="wf-description">{ins.description}</div>

              <div className="wf-metrics">
                <span>
                  {ins.metric_name}: <b>{ins.metric_value.toFixed(1)}</b>
                </span>
                {ins.baseline_value !== null && (
                  <span className="wf-baseline">
                    (baseline {ins.baseline_value.toFixed(1)})
                  </span>
                )}
                <span className="wf-sample">{ins.sample_size} samples</span>
              </div>

              {expandedId === ins.insight_id && ins.evidence?.rows && (
                <motion.div
                  className="wf-evidence"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                >
                  <table>
                    <thead>
                      <tr>
                        <th>Group</th>
                        <th>Value</th>
                        {ins.evidence.rows[0]?.count !== undefined && <th>N</th>}
                      </tr>
                    </thead>
                    <tbody>
                      {ins.evidence.rows.map((r, i) => (
                        <tr key={i}>
                          <td>{r.label}</td>
                          <td>{r.value.toFixed(1)}</td>
                          {r.count !== undefined && <td>{r.count}</td>}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </motion.div>
              )}

              {onAction && (
                <button
                  className="wf-action"
                  onClick={() => onAction(ins.action_type, ins.action_payload)}
                >
                  {ins.action_label} →
                </button>
              )}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
};
