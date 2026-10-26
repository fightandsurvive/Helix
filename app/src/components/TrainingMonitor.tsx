import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

interface ProgressRow {
  generation: number;
  stage: string;
  stage_progress: number;
  games_completed: number;
  games_target: number;
  policy_loss?: number;
  value_loss?: number;
  total_loss?: number;
  elo_estimate?: number;
  elo_stderr?: number;
  sprt_llr?: number;
  sprt_upper?: number;
  sprt_lower?: number;
  sprt_status?: string;
  eta_seconds?: number;
  message?: string;
}

interface TrainingState {
  status: string;
  current_generation: number;
  progress: ProgressRow | null;
  elo_history: [number, number, number][];
  recent_losses: [number, number, number][];
}

const STAGE_LABEL: Record<string, string> = {
  idle: "Idle",
  selfplay: "Self-play",
  training: "Training",
  gating: "SPRT Gate",
  adapters: "Adapters",
  pruning: "Pruning",
  error: "Error",
};

export const TrainingMonitor = () => {
  const [state, setState] = useState<TrainingState | null>(null);

  useEffect(() => {
    const poll = () =>
      invoke<TrainingState>("get_training_state").then(setState).catch(console.error);
    poll();
    const id = setInterval(poll, 2000);
    return () => clearInterval(id);
  }, []);

  if (!state) {
    return <div className="training-monitor">Loading training state…</div>;
  }

  const p = state.progress;
  const fmtEta = (sec?: number) => {
    if (!sec) return "—";
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    return h ? `${h}h ${m}m` : `${m}m`;
  };

  return (
    <div className="training-monitor">
      <div className="tm-header">
        <h3>Training Monitor</h3>
        <span className={`tm-status tm-${state.status}`}>{state.status}</span>
      </div>

      {!p && state.status === "idle" && (
        <div className="tm-empty">
          No active run. Start one with:
          <pre>python -m training.orchestrator</pre>
        </div>
      )}

      {p && (
        <>
          <div className="tm-row">
            <span>Generation</span>
            <b>{p.generation}</b>
          </div>
          <div className="tm-row">
            <span>Stage</span>
            <b>{STAGE_LABEL[p.stage] ?? p.stage}</b>
          </div>
          <div className="tm-progress-bar">
            <div className="tm-progress-fill" style={{ width: `${p.stage_progress * 100}%` }} />
          </div>
          <div className="tm-row small">
            <span>ETA</span>
            <span>{fmtEta(p.eta_seconds)}</span>
          </div>
          {p.games_target > 0 && (
            <div className="tm-row small">
              <span>Games</span>
              <span>
                {p.games_completed.toLocaleString()} / {p.games_target.toLocaleString()}
              </span>
            </div>
          )}
          {p.total_loss != null && (
            <div className="tm-row small">
              <span>Loss</span>
              <span>{p.total_loss.toFixed(4)}</span>
            </div>
          )}
          {p.sprt_llr != null && (
            <div className="tm-sprt">
              <div className="tm-row small">
                <span>SPRT LLR</span>
                <span className="mono">{p.sprt_llr.toFixed(2)}</span>
              </div>
              <div className="tm-row small">
                <span>Status</span>
                <span>{p.sprt_status ?? "—"}</span>
              </div>
            </div>
          )}
          {p.message && <div className="tm-msg">{p.message}</div>}
        </>
      )}

      {state.recent_losses.length > 2 && (
        <div className="tm-chart">
          <div className="tm-chart-label">Loss (recent)</div>
          <ResponsiveContainer width="100%" height={80}>
            <LineChart
              data={state.recent_losses.map(([, step, loss]) => ({ step, loss }))}
            >
              <XAxis dataKey="step" hide />
              <YAxis hide domain={["auto", "auto"]} />
              <Tooltip
                contentStyle={{ background: "#17171c", border: "1px solid #333" }}
              />
              <Line
                type="monotone"
                dataKey="loss"
                stroke="#22d3ee"
                dot={false}
                strokeWidth={1.5}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {state.elo_history.length > 1 && (
        <div className="tm-chart">
          <div className="tm-chart-label">Elo Trajectory</div>
          <ResponsiveContainer width="100%" height={100}>
            <LineChart data={state.elo_history.map(([g, e]) => ({ g, elo: e }))}>
              <XAxis dataKey="g" tick={{ fontSize: 10, fill: "#6b7280" }} />
              <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "#6b7280" }} />
              <Tooltip contentStyle={{ background: "#17171c", border: "1px solid #333" }} />
              <Line type="monotone" dataKey="elo" stroke="#86efac" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
};
