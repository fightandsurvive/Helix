import { useState } from "react";
import { useInvoke } from "../hooks/useInvoke";
import { motion, AnimatePresence } from "framer-motion";

interface Voice {
  name: string;
  kind: "legend" | "oracle";
  best_move_san: string;
  eval_cp: number;
  confidence?: number;
  differs_from_sf?: boolean;
  pv?: string[];
}

interface Panel {
  fen: string;
  side_to_move: string;
  base: { best_move_san: string; eval: number };
  voices: Voice[];
  agreements: { move: string; voices: string[]; count: number }[];
  sf_depth: number;
}

export const LegendsPanel = ({ fen }: { fen: string }) => {
  const [expanded, setExpanded] = useState(false);
  const [commentary, setCommentary] = useState<string | null>(null);
  const [loadingCommentary, setLoadingCommentary] = useState(false);

  const { data, loading, error } = useInvoke<{ panel: Panel }>(
    "panel_analysis",
    { fen },
    { immediate: expanded, deps: [fen, expanded] }
  );

  const requestCommentary = async () => {
    setLoadingCommentary(true);
    try {
      const res = await import("@tauri-apps/api/core").then((m) =>
        m.invoke<{ panel: Panel; commentary?: string }>("panel_commentary", { fen })
      );
      setCommentary(res.commentary ?? "(no commentary)");
    } catch (e) {
      setCommentary(`(commentary failed: ${e})`);
    } finally {
      setLoadingCommentary(false);
    }
  };

  if (!expanded) {
    return (
      <button className="legends-invoke" onClick={() => setExpanded(true)}>
        ◈ Consult the Legends
      </button>
    );
  }

  const panel = data?.panel;

  return (
    <div className="legends-panel">
      <div className="lp-header">
        <span className="lp-title">THE LEGENDS</span>
        <button className="lp-collapse" onClick={() => setExpanded(false)}>
          collapse
        </button>
      </div>

      {loading && <div className="lp-loading">Consulting seven minds…</div>}
      {error && <div className="lp-error">{error}</div>}

      <AnimatePresence>
        {panel && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="lp-body">
            <div className="lp-base">
              <span className="lp-base-label">Base engine</span>
              <span className="lp-base-move">{panel.base.best_move_san ?? "—"}</span>
              <span className="lp-base-eval">
                {panel.base.eval > 0 ? "+" : ""}
                {panel.base.eval.toFixed(2)}
              </span>
            </div>

            <div className="lp-voices">
              {panel.voices.map((v) => (
                <div
                  key={v.name}
                  className={`voice-card ${v.kind === "oracle" ? "oracle" : "legend"} ${
                    v.differs_from_sf ? "differs" : ""
                  }`}
                >
                  <div className="vc-name">{v.name}</div>
                  <div className="vc-move">{v.best_move_san ?? "—"}</div>
                  <div className="vc-eval">
                    {v.eval_cp > 0 ? "+" : ""}
                    {(v.eval_cp / 100).toFixed(2)}
                  </div>
                  {v.confidence !== undefined && (
                    <div className="vc-confidence">
                      {(v.confidence * 100).toFixed(0)}% conf
                    </div>
                  )}
                  {v.differs_from_sf && <div className="vc-tag">≠ SF</div>}
                </div>
              ))}
            </div>

            {panel.agreements.length > 0 && (
              <div className="lp-agreements">
                {panel.agreements.map((a) => (
                  <div key={a.move} className="lp-agreement">
                    <span className="lp-agg-move">{a.move}</span>
                    <span className="lp-agg-voices">{a.voices.join(" · ")}</span>
                  </div>
                ))}
              </div>
            )}

            <button
              className="lp-commentary-btn"
              onClick={requestCommentary}
              disabled={loadingCommentary}
            >
              {loadingCommentary ? "Synthesizing…" : "Explain"}
            </button>

            {commentary && <div className="lp-commentary">{commentary}</div>}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
