import { useEffect, useRef, useState } from "react";
import type { WsMessage } from "./types";

export interface BacktestProgress {
  pct: number;
  stage: string | null;
  log: string[]; // capped to last 50 lines
  done: boolean;
  error: string | null;
}

const LOG_CAP = 50;

function wsUrl(runId: string): string {
  // Prefer explicit override (useful for tests / split deployments); fall back to
  // same-origin relative URL so Vite's dev proxy can forward it.
  const override = import.meta.env.VITE_WS_BASE as string | undefined;
  if (override) {
    return `${override.replace(/\/$/, "")}/backtest/${encodeURIComponent(runId)}`;
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/backtest/${encodeURIComponent(runId)}`;
}

export function useBacktestProgress(runId: string): BacktestProgress {
  const [pct, setPct] = useState(0);
  const [stage, setStage] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Track whether we've already retried once on an unexpected close so we don't
  // keep reconnecting forever.
  const retriedRef = useRef(false);
  const closedByServerRef = useRef(false);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!runId) return;
    retriedRef.current = false;
    closedByServerRef.current = false;

    let cancelled = false;

    const connect = () => {
      const ws = new WebSocket(wsUrl(runId));
      socketRef.current = ws;

      ws.onmessage = (ev: MessageEvent<string>) => {
        let msg: WsMessage;
        try {
          msg = JSON.parse(ev.data) as WsMessage;
        } catch {
          return;
        }
        switch (msg.type) {
          case "progress":
            setPct(msg.pct);
            setStage(msg.stage);
            if (msg.log) {
              const line = msg.log;
              setLog((prev) => {
                const next = prev.concat(line);
                return next.length > LOG_CAP
                  ? next.slice(next.length - LOG_CAP)
                  : next;
              });
            }
            break;
          case "stage":
            setStage(msg.stage);
            setPct(msg.pct);
            break;
          case "done":
            setPct(100);
            setDone(true);
            closedByServerRef.current = true;
            break;
          case "error":
            setError(msg.message);
            closedByServerRef.current = true;
            break;
        }
      };

      ws.onclose = () => {
        if (cancelled) return;
        if (closedByServerRef.current) return;
        // Unexpected close: reconnect once, then give up.
        if (!retriedRef.current) {
          retriedRef.current = true;
          connect();
        } else {
          setError((e) => e ?? "WebSocket connection lost");
        }
      };

      ws.onerror = () => {
        // onclose will fire next; rely on that for retry logic.
      };
    };

    connect();

    return () => {
      cancelled = true;
      const s = socketRef.current;
      if (s && s.readyState <= WebSocket.OPEN) {
        s.close();
      }
      socketRef.current = null;
    };
  }, [runId]);

  return { pct, stage, log, done, error };
}
