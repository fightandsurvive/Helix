import { useEffect, useState } from "react";
import { useInvoke } from "./hooks/useInvoke";
import { useCapabilities } from "./hooks/useCapabilities";
import { Capability } from "./components/Capability";
import { ChessBoardView } from "./components/ChessBoardView";
import { CoachPanel } from "./components/CoachPanel";
import { GodModePanel } from "./components/GodModePanel";
import { OpeningExplorer } from "./components/OpeningExplorer";
import { OpeningTrainer } from "./components/OpeningTrainer";
import { PuzzleTrainer } from "./components/PuzzleTrainer";
import { GamesAnalytics } from "./components/GamesAnalytics";
import { WeaknessFinder } from "./components/WeaknessFinder";
import { PlayTab } from "./components/PlayTab";
import { PositionSetup } from "./components/PositionSetup";
import { DailyPuzzle } from "./components/DailyPuzzle";
import { EvalGraph } from "./components/EvalGraph";

type Tab = "play" | "insights" | "openings" | "puzzles" | "analysis" | "setup" | "training" | "engine";
const TABS = [
  { id: "play", label: "Play" },
  { id: "insights", label: "Insights" },
  { id: "openings", label: "Openings" },
  { id: "puzzles", label: "Puzzles" },
  { id: "analysis", label: "Analysis" },
  { id: "setup", label: "Setup" },
  { id: "training", label: "Training" },     // gated
  { id: "engine", label: "Engine" },         // gated
];

export default function App() {
  const [fen, setFen] = useState("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
  const [tab, setTab] = useState<Tab>("play");
  const [godMode, setGodMode] = useState(false);
  const [participant, setParticipant] = useState("");
  const [source, setSource] = useState<"personal" | "eval">("personal");
  const [playMode, setPlayMode] = useState<"analyze" | "game">("analyze");
  const [activeGameId, setActiveGameId] = useState<number | null>(null);
  const [currentPly, setCurrentPly] = useState(0);

  const { data: caps } = useCapabilities();
  const { data: activeProfile } = useInvoke<number | null>("get_active_profile");

  return (
    <div className="app-shell">
      <header className="top-bar">
        <h1 className="brand">HELIX</h1>
        <span className="subtitle">Chess Workstation</span>
        {caps && (
          <span className="cap-badge" title={`GPU: ${caps.gpu_name ?? "none"}`}>
            {caps.engine_available ? "🟢 Engine" : "⚪ Engine"} ·{" "}
            {caps.coach_llm_available ? "🟢 LLM" : "⚪ LLM"} ·{" "}
            {caps.training_available ? "🟢 Training" : "⚪ Training"}
          </span>
        )}
        <nav className="top-tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id} role="tab" aria-selected={tab === t.id}
              className={`tab ${tab === t.id ? "active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      {tab === "play" && (
        <div className="main-grid">
          <aside className="left-panel">
            <OpeningExplorer currentFen={fen} />
            <div className="god-mode-toggle">
              <label>
                <input type="checkbox" checked={godMode}
                       onChange={(e) => setGodMode(e.target.checked)} />
                <span>God Mode</span>
              </label>
            </div>
            {godMode && (
              <GodModePanel
                fen={fen}
                onSelectMove={(uci) => {
                  const { Chess } = require("chess.js");
                  const g = new Chess(fen);
                  try {
                    g.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] || "q" });
                    setFen(g.fen());
                  } catch {}
                }}
              />
            )}
          </aside>
          <main className="board-area">
            <div className="play-mode-toggle">
              <button className={playMode === "analyze" ? "active" : ""}
                      onClick={() => setPlayMode("analyze")}>Analyze</button>
              <button className={playMode === "game" ? "active" : ""}
                      onClick={() => setPlayMode("game")}>Play vs SF</button>
            </div>
            {playMode === "analyze" ? (
              <>
                <ChessBoardView fen={fen} setFen={setFen}
                                gameId={activeGameId} plyNumber={currentPly}
                                interactiveAnnotations={true} />
                <div className="annotation-hint">
                  Right-drag to draw arrows · Click to highlight · Ctrl-click to clear
                </div>
                {activeGameId && (
                  <EvalGraph gameId={activeGameId} currentPly={currentPly}
                             onJumpToPly={(ply, newFen) => { setCurrentPly(ply); setFen(newFen); }} />
                )}
              </>
            ) : (
              activeProfile !== null ? (
                <PlayTab profileId={activeProfile}
                         onGameSaved={(id) => { setActiveGameId(id); setPlayMode("analyze"); }} />
              ) : (
                <div className="analysis-hint">Import a profile first to play.</div>
              )
            )}
          </main>
          <aside className="right-panel">
            <CoachPanel fen={fen} />
          </aside>
        </div>
      )}

      {tab === "insights" && (
        <div className="main-grid single-center">
          {activeProfile !== null ? (
            <WeaknessFinder profileId={activeProfile} onAction={(type, payload) => {
              if (type === "train_opening" || type === "practice_phase") setTab("openings");
              else if (type === "practice_puzzles") setTab("puzzles");
              else if (type === "review_games") setTab("analysis");
            }} />
          ) : (
            <div className="analysis-hint">Import a profile first in the Analysis tab.</div>
          )}
        </div>
      )}

      {tab === "openings" && <div className="main-grid single-center"><OpeningTrainer /></div>}

      {tab === "puzzles" && (
        <div className="main-grid single-center">
          {activeProfile !== null && (
            <div style={{ marginBottom: 20 }}>
              <DailyPuzzle profileId={activeProfile} />
            </div>
          )}
          <PuzzleTrainer />
        </div>
      )}

      {tab === "analysis" && (
        <div className="main-grid single-center">
          <div className="analysis-controls">
            <select value={source} onChange={(e) => setSource(e.target.value as "personal" | "eval")}>
              <option value="personal">Personal</option>
              <option value="eval">Engine Eval</option>
            </select>
            <input type="text" placeholder="Username or engine name"
                   value={participant} onChange={(e) => setParticipant(e.target.value)} />
          </div>
          {participant ? <GamesAnalytics participant={participant} source={source} />
                       : <div className="analysis-hint">Enter a username or engine name.</div>}
        </div>
      )}

      {tab === "training" && (
        <div className="main-grid single-center">
          <Capability
            enabled={caps?.training_available ?? false}
            reason={caps?.training_reason ?? "Probing capabilities…"}
            title="Training Pipeline"
          >
            <TrainingMonitor />
          </Capability>
        </div>
      )}

      {tab === "engine" && (
        <div className="main-grid single-center">
          <Capability
            enabled={caps?.engine_available ?? false}
            reason={caps?.engine_reason ?? "Probing capabilities…"}
            title="Trinstinctus Engine"
          >
            <StyleWeightDashboard profile="balanced" onProfileChange={() => {}} />
          </Capability>
          <Capability
            enabled={caps?.engine_available ?? false}
            reason={caps?.engine_reason ?? "Probing capabilities…"}
            title="Legends Panel"
          >
            <LegendsPanel fen={fen} />
          </Capability>
        </div>
      )}
      {tab === "setup" && (
        <div className="main-grid single-center">
          <PositionSetup profileId={activeProfile ?? 1}
                         onLoad={(newFen) => { setFen(newFen); setTab("play"); }} />
        </div>
      )}
    </div>
  );
}
