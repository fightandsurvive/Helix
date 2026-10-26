import { useState } from "react";
import { Chess } from "chess.js";
import { invoke } from "@tauri-apps/api/core";

const PRESET_POSITIONS = [
  { name: "Starting position", fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1" },
  { name: "Lucena position", fen: "1K1k4/1P6/8/8/8/8/r7/2R5 w - - 0 1" },
  { name: "Philidor position", fen: "8/8/8/8/4k3/r7/5R2/4K3 w - - 0 1" },
  { name: "Réti endgame study", fen: "7K/8/k1P5/7p/8/8/8/8 w - - 0 1" },
];

export const PositionSetup = ({
  profileId,
  onLoad,
}: {
  profileId: number;
  onLoad: (fen: string) => void;
}) => {
  const [fen, setFen] = useState("");
  const [error, setError] = useState("");
  const [savedName, setSavedName] = useState("");

  const tryLoad = () => {
    try {
      new Chess(fen);
      setError("");
      onLoad(fen);
    } catch (e) {
      setError(`Invalid FEN: ${e}`);
    }
  };

  const saveToLibrary = async () => {
    if (!savedName.trim()) { setError("Name required"); return; }
    try {
      new Chess(fen);
      await invoke("save_position", {
        profileId, fen, name: savedName, notes: null, tags: [],
      });
      setSavedName("");
      setError("");
    } catch (e) {
      setError(`Save failed: ${e}`);
    }
  };

  return (
    <div className="position-setup">
      <h3>Position Setup</h3>
      <div className="ps-presets">
        {PRESET_POSITIONS.map((p) => (
          <button key={p.name} className="ps-preset" onClick={() => { setFen(p.fen); onLoad(p.fen); }}>
            {p.name}
          </button>
        ))}
      </div>
      <div className="ps-input-row">
        <input
          type="text"
          placeholder="Paste FEN here"
          value={fen}
          onChange={(e) => setFen(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && tryLoad()}
        />
        <button onClick={tryLoad}>Load</button>
      </div>
      {error && <div className="ps-error">{error}</div>}
      <div className="ps-save-row">
        <input
          type="text"
          placeholder="Name this position"
          value={savedName}
          onChange={(e) => setSavedName(e.target.value)}
        />
        <button onClick={saveToLibrary}>Save to Library</button>
      </div>
    </div>
  );
};
