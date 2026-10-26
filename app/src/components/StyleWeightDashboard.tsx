import { useEffect, useState } from "react";
import { useInvoke } from "../hooks/useInvoke";
import { invoke } from "@tauri-apps/api/core";

interface Weights {
  profile: string;
  morphy: number;
  petrosian: number;
  tal_kasparov: number;
  fischer: number;
  fischer_endgame: number;
  carlsen_endgame: number;
  temperature: number;
  contempt: number;
}

const PROFILES = [
  "balanced", "romantic", "iron_fortress", "anti_stockfish",
  "morphy", "petrosian", "tal", "kasparov", "fischer", "carlsen",
];

export const StyleWeightDashboard = ({
  profile,
  onProfileChange,
}: {
  profile: string;
  onProfileChange: (p: string) => void;
}) => {
  const { data: adapterCount } = useInvoke<number>("count_adapters");
  const { data: weights } = useInvoke<Weights>(
    "get_style_weights",
    { profile },
    { immediate: adapterCount !== null && adapterCount > 0, deps: [profile, adapterCount] }
  );
  const [local, setLocal] = useState<Weights | null>(null);

  useEffect(() => {
    if (weights) setLocal(weights);
  }, [weights]);

  const disabled = adapterCount === null || adapterCount === 0;

  const update = async (field: keyof Weights, value: number) => {
    if (!local) return;
    const next = { ...local, [field]: value };
    setLocal(next);
    try {
      await invoke("set_style_weights", { w: next });
    } catch (e) {
      console.error("set_style_weights failed:", e);
    }
  };

  const tooltip = disabled
    ? "Train personality adapters first:\n\n  python -m training.train_adapters --base checkpoints/distilled.pt --legend <name>\n\nOr run the orchestrator — it trains adapters automatically when the base plateaus."
    : "";

  const sliders: { key: keyof Weights; label: string; max: number }[] = [
    { key: "morphy", label: "Morphy", max: 2 },
    { key: "petrosian", label: "Petrosian", max: 2 },
    { key: "tal_kasparov", label: "Tal / Kasparov", max: 2 },
    { key: "fischer", label: "Fischer", max: 2 },
    { key: "fischer_endgame", label: "Fischer Endgame", max: 2 },
    { key: "carlsen_endgame", label: "Carlsen Endgame", max: 2 },
    { key: "contempt", label: "Contempt", max: 0.5 },
  ];

  return (
    <div className={`style-dashboard ${disabled ? "is-disabled" : ""}`} title={tooltip}>
      <h3>Legend Weight Customizer</h3>
      {disabled && (
        <div className="adapter-hint">
          Adapters not trained yet — base engine runs at full strength in{" "}
          <em>balanced</em> mode.
        </div>
      )}
      {!disabled && local && (
        <>
          <select value={profile} onChange={(e) => onProfileChange(e.target.value)}>
            {PROFILES.map((p) => (
              <option key={p} value={p}>
                {p.replace(/_/g, " ")}
              </option>
            ))}
          </select>
          {sliders.map(({ key, label, max }) => (
            <div key={key} className="slider-row">
              <label>{label}</label>
              <input
                type="range"
                min={0}
                max={max}
                step={0.05}
                value={local[key] as number}
                onChange={(e) => update(key, parseFloat(e.target.value))}
              />
              <span>{(local[key] as number).toFixed(2)}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
};
