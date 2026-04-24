import { create } from "zustand";
import * as api from "./api";
import type {
  GateReportSummary,
  RunDetail,
  RunSummary,
  StrategyEntry,
} from "./types";

export interface AppStore {
  runs: RunSummary[];
  runsLoaded: boolean;
  refreshRuns: () => Promise<void>;

  currentRun: RunDetail | null;
  loadRun: (id: string) => Promise<void>;

  compareIds: [string | null, string | null];
  setCompareA: (id: string | null) => void;
  setCompareB: (id: string | null) => void;

  gateReports: GateReportSummary[];
  refreshGateReports: () => Promise<void>;

  strategies: StrategyEntry[];
  strategiesLoaded: boolean;
  refreshStrategies: () => Promise<void>;
}

export const useAppStore = create<AppStore>((set) => ({
  runs: [],
  runsLoaded: false,
  refreshRuns: async () => {
    const res = await api.listRuns();
    set({ runs: res.runs, runsLoaded: true });
  },

  currentRun: null,
  loadRun: async (id: string) => {
    const detail = await api.getRun(id);
    set({ currentRun: detail });
  },

  compareIds: [null, null],
  setCompareA: (id) => set((s) => ({ compareIds: [id, s.compareIds[1]] })),
  setCompareB: (id) => set((s) => ({ compareIds: [s.compareIds[0], id] })),

  gateReports: [],
  refreshGateReports: async () => {
    const res = await api.listGateReports();
    set({ gateReports: res.reports });
  },

  strategies: [],
  strategiesLoaded: false,
  refreshStrategies: async () => {
    const res = await api.listStrategies();
    set({ strategies: res.strategies, strategiesLoaded: true });
  },
}));
