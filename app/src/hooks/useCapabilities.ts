// app/src/hooks/useCapabilities.ts
import { useInvoke } from "./useInvoke";

export interface Capabilities {
  engine_available: boolean;
  training_available: boolean;
  coach_llm_available: boolean;
  stockfish_available: boolean;
  syzygy_available: boolean;
  gpu_name: string | null;
  gpu_vram_gb: number | null;
  is_blackwell: boolean;
  engine_reason: string;
  training_reason: string;
  coach_reason: string;
}

export const useCapabilities = () =>
  useInvoke<Capabilities>("get_capabilities", {}, { immediate: true });
