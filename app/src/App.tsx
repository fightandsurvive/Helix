import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";
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
import { LegendsPanel } from "./components/LegendsPanel";
import { StyleWeightDashboard } from "./components/StyleWeightDashboard";
import { TrainingMonitor } from "./components/TrainingMonitor";

type Tab = "play" | "insights" | "openings" | "puzzles" | "analysis" | "setup" | "training" | "engine";

const TABS: { id: Tab; label: string }[] = [
    { id: "play", label: "Play" },
    { id: "insights", label: "Insights" },
    { id: "openings", label: "Openings" },
    { id: "puzzles", label: "Puzzles" },
    { id: "analysis", label: "Analysis" },
    { id: "setup", label: "Setup" },
    { id: "training", label: "Training" },
    { id: "engine", label: "Engine" },
];

export default function App() {
    const [fen, setFen] = useState("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
    const [tab, setTab] = useState<Tab>("play");
    const [godMode, setGodMode] = useState(false);
    const [source, setSource] = useState<"personal" | "eval">("personal");
    const [playMode, setPlayMode] = useState<"analyze" | "game">("analyze");
    const [activeGameId, setActiveGameId] = useState<number | null>(null);
    const [currentPly, setCurrentPly] = useState(0);

    // Analysis tab state
    const [platform, setPlatform] = useState<"lichess" | "chesscom">("lichess");
    const [username, setUsername] = useState("");
    const [importing, setImporting] = useState(false);
    const [importMsg, setImportMsg] = useState("");

    const { data: caps } = useCapabilities();
    const { data: activeProfile, refetch: refetchProfile } = useInvoke<number | null>("get_active_profile");

    const handleImport = async () => {
        if (!username.trim()) return;
        setImporting(true);
        setImportMsg("");
        try {
            const res = await invoke<{ profile_id: number; games_imported: number; message: string }>(
                "import_personal_games",
                { platform, username: username.trim() }
            );
            setImportMsg(`✓ ${res.message}`);
            await refetchProfile();
        } catch (e) {
            setImportMsg(`✗ ${e}`);
        } finally {
            setImporting(false);
        }
    };

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
                            key={t.id}
                            role="tab"
                            aria-selected={tab === t.id}
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
                                <input
                                    type="checkbox"
                                    checked={godMode}
                                    onChange={(e) => setGodMode(e.target.checked)}
                                />
                                <span>God Mode</span>
                            </label>
                        </div>
                        {godMode && <GodModePanel fen={fen} onSelectMove={(uci) => {
                            const g = new (require("chess.js").Chess)(fen);
                            try {
                                g.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] || "q" });
                                setFen(g.fen());
                            } catch { }
                        }} />}
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
                                    gameId={activeGameId} plyNumber={currentPly} />
                                <div className="annotation-hint">
                                    Left-click moves · Click-click also moves · Right-click highlights
                                </div>
                                {activeGameId && (
                                    <EvalGraph gameId={activeGameId} currentPly={currentPly}
                                        onJumpToPly={(ply, newFen) => { setCurrentPly(ply); setFen(newFen); }} />
                                )}
                            </>
                        ) : activeProfile !== null ? (
                            <PlayTab profileId={activeProfile}
                                onGameSaved={(id) => { setActiveGameId(id); setPlayMode("analyze"); }} />
                        ) : (
                            <div className="analysis-hint">
                                Import a profile first (Analysis tab → Import).
                            </div>
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
                        <WeaknessFinder profileId={activeProfile} onAction={(type) => {
                            if (type === "train_opening" || type === "practice_phase") setTab("openings");
                            else if (type === "practice_puzzles") setTab("puzzles");
                            else if (type === "review_games") setTab("analysis");
                        }} />
                    ) : (
                        <div className="analysis-hint">Import a profile first (Analysis tab → Import).</div>
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
                        <select value={platform}
                            onChange={(e) => setPlatform(e.target.value as "lichess" | "chesscom")}>
                            <option value="lichess">Lichess</option>
                            <option value="chesscom">Chess.com</option>
                        </select>
                        <input type="text" placeholder="Username"
                            value={username} onChange={(e) => setUsername(e.target.value)} />
                        <button onClick={handleImport} disabled={importing || !username.trim()}>
                            {importing ? "Importing…" : "Import Games"}
                        </button>
                    </div>
                    {importMsg && <div className="import-status">{importMsg}</div>}

                    <div className="analysis-controls" style={{ marginTop: 16 }}>
                        <select value={source}
                            onChange={(e) => setSource(e.target.value as "personal" | "eval")}>
                            <option value="personal">Personal</option>
                            <option value="eval">Engine Eval</option>
                        </select>
                        <input type="text" placeholder="Username for stats"
                            value={username} onChange={(e) => setUsername(e.target.value)} />
                    </div>
                    {username ? <GamesAnalytics participant={username} source={source} />
                        : <div className="analysis-hint">Import or enter username to view analytics.</div>}
                </div>
            )}

            {tab === "setup" && (
                <div className="main-grid single-center">
                    <PositionSetup profileId={activeProfile ?? 1}
                        onLoad={(newFen) => { setFen(newFen); setTab("play"); }} />
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
                        <StyleWeightDashboard profile="balanced" onProfileChange={() => { }} />
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
        </div>
    );
}
