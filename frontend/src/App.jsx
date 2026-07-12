import { useState, useRef, useCallback } from "react";

const ZONES = ["zone_1", "zone_2", "zone_3", "zone_4", "zone_5"];
const GAS_DANGER_THRESHOLD = 80;
const WS_URL = "ws://localhost:8000/ws/stream";
const VIDEO_URL = "http://localhost:8000/media/annotated.mp4";

function statusColor(score) {
  if (score >= 60) return "var(--danger)";
  if (score >= 30) return "var(--caution)";
  return "var(--safe)";
}

function GasGauge({ zone, value }) {
  const pct = Math.min(value / (GAS_DANGER_THRESHOLD + 20), 1);
  const angle = -120 + pct * 240; // -120deg to +120deg sweep
  const color = value >= GAS_DANGER_THRESHOLD ? "var(--danger)"
    : value >= 30 ? "var(--caution)" : "var(--safe)";

  return (
    <div className="gauge">
      <svg viewBox="0 0 200 140" width="100%">
        <path d="M 30 120 A 80 80 0 1 1 170 120" fill="none"
          stroke="var(--grid)" strokeWidth="14" strokeLinecap="round" />
        <path d="M 30 120 A 80 80 0 1 1 170 120" fill="none"
          stroke={color} strokeWidth="14" strokeLinecap="round"
          strokeDasharray={`${pct * 377} 377`}
          style={{ transition: "stroke-dasharray 0.3s linear, stroke 0.3s linear" }} />
        <line x1="100" y1="120" x2={100 + 62 * Math.sin((angle * Math.PI) / 180)}
          y2={120 - 62 * Math.cos((angle * Math.PI) / 180)}
          stroke="var(--text)" strokeWidth="3" strokeLinecap="round"
          style={{ transition: "all 0.3s linear" }} />
        <circle cx="100" cy="120" r="6" fill="var(--text)" />
      </svg>
      <div className="gauge-reading">
        <span className="gauge-value">{value.toFixed(1)}</span>
        <span className="gauge-unit">ppm · {zone.replace("zone_", "ZONE ")}</span>
      </div>
    </div>
  );
}

export default function App() {
  const [status, setStatus] = useState("offline"); // "offline" | "live" | "complete"
  const [running, setRunning] = useState(false);
  const [zoneData, setZoneData] = useState({});   // zone -> {gas, temperature, pressure}
  const [riskScores, setRiskScores] = useState({}); // zone -> score
  const [alert, setAlert] = useState(null);
  const [ragContext, setRagContext] = useState(null); // null = nothing yet, "loading" = waiting, or the payload
  const [log, setLog] = useState([]);
  const [cvStatus, setCvStatus] = useState(null); // {worker_in_zone, violation} | null
  const [videoError, setVideoError] = useState(null);
  const wsRef = useRef(null);
  const videoRef = useRef(null);

  const startDemo = useCallback(() => {
    if (wsRef.current) wsRef.current.close();
    setAlert(null);
    setRagContext(null);
    setLog([]);
    setCvStatus(null);
    setZoneData({});
    setRiskScores({});
    setRunning(true);
    setStatus("live");

    if (videoRef.current) {
      // Don't touch currentTime here — setting it before the video's
      // metadata has loaded can throw/trigger a real error event in some
      // browsers, which then leaves the video stuck in an error state.
      // play() alone is safe to call any time; the browser queues it.
      videoRef.current.play().catch(() => {}); // autoplay may be blocked until user interaction; button click counts
    }

    const ws = new WebSocket(`${WS_URL}?ramp_seconds=60&interval=0.5&zone=zone_4`);
    wsRef.current = ws;

    ws.onopen = () => setStatus("live");
    ws.onclose = () => {
      setRunning(false);
      // Only fall back to "offline" if the scenario didn't finish cleanly —
      // a clean finish already set status to "complete" via scenario_complete.
      setStatus((prev) => (prev === "complete" ? "complete" : "offline"));
    };
    ws.onerror = () => { setRunning(false); setStatus("offline"); };

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);

      if (msg.type === "reading") {
        setZoneData((prev) => ({
          ...prev,
          [msg.zone]: { ...prev[msg.zone], [msg.sensor_type]: msg.value },
        }));
      } else if (msg.type === "risk_update") {
        setRiskScores((prev) => ({ ...prev, [msg.zone]: msg.risk_score }));
      } else if (msg.type === "cv_status") {
        setCvStatus(msg);
      } else if (msg.type === "compound_risk_alert") {
        setAlert(msg);
        setRagContext("loading");
        setLog((prev) => [msg, ...prev]);
      } else if (msg.type === "rag_context") {
        setRagContext(msg);
      } else if (msg.type === "scenario_complete") {
        setRunning(false);
        setStatus("complete");
      }
    };
  }, []);

  const focusZone = "zone_4";
  const focusGas = zoneData[focusZone]?.gas ?? 0;

  return (
    <div className="app">
      <style>{css}</style>

      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" />
          SAFENET <span className="brand-sub">CONTROL</span>
        </div>
        <div className="topbar-right">
          <span className={`dot dot-${status}`} />
          {status === "live" ? "LIVE" : status === "complete" ? "DEMO COMPLETE" : "OFFLINE"}
          <button className="btn" onClick={startDemo} disabled={running}>
            {running ? "RUNNING…" : "RUN DEMO"}
          </button>
        </div>
      </header>

      {alert && (
        <div className="alert-banner">
          <div className="hazard-stripes" />
          <div className="alert-content">
            <strong>COMPOUND RISK ALERT</strong> — {alert.zone.replace("zone_", "Zone ")}:
            {" "}{alert.reason}
          </div>
        </div>
      )}

      <main className="grid">
        <section className="panel zones">
          <h2>Zone Status</h2>
          <div className="zone-grid">
            {ZONES.map((zone) => {
              const score = riskScores[zone] ?? 0;
              const gas = zoneData[zone]?.gas;
              return (
                <div key={zone} className="zone-tile" style={{ "--tile-color": statusColor(score) }}>
                  <div className="zone-name">{zone.replace("zone_", "Z")}</div>
                  <div className="zone-score">{Math.round(score)}</div>
                  <div className="zone-gas">{gas != null ? `${gas.toFixed(1)} ppm` : "—"}</div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="panel gauge-panel">
          <h2>Gas Level — {focusZone.replace("zone_", "Zone ")}</h2>
          <GasGauge zone={focusZone} value={focusGas} />
        </section>

        <section className="panel cv-panel">
          <h2>Camera — {focusZone.replace("zone_", "Zone ")} (CV Module)</h2>
          <div className="video-wrap">
            <video
              ref={videoRef}
              src={VIDEO_URL}
              muted
              loop
              playsInline
              className="cv-video"
              onError={(e) => {
                const mediaError = e.currentTarget.error;
                console.error("Video load error:", mediaError?.code, mediaError?.message);
                setVideoError(
                  `Could not load video (code ${mediaError?.code ?? "?"}) — retrying…`
                );
                // Auto-recover: reload the source and try again once.
                // Handles the case where play() was called before the
                // browser had fully initialized the media pipeline.
                setTimeout(() => {
                  if (videoRef.current) {
                    videoRef.current.load();
                    videoRef.current.play().catch(() => {});
                  }
                }, 500);
              }}
              onLoadedData={() => setVideoError(null)}
            />
            {videoError && <div className="cv-video-error">{videoError}</div>}
            {cvStatus?.worker_in_zone && (
              <div className={`cv-badge ${cvStatus.violation ? "cv-badge-violation" : "cv-badge-present"}`}>
                {cvStatus.violation ? `⚠ ${cvStatus.violation}` : "Worker in zone"}
              </div>
            )}
          </div>
        </section>

        <section className="panel log">
          <h2>Alert Log</h2>
          {log.length === 0 ? (
            <div className="log-empty">No alerts yet. Run the demo to begin monitoring.</div>
          ) : (
            <ul>
              {log.map((a, i) => (
                <li key={i}>
                  <span className="log-time">{new Date(a.timestamp).toLocaleTimeString()}</span>
                  <span className="log-zone">{a.zone}</span>
                  <span className="log-reason">{a.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        {ragContext && (
          <section className="panel rag">
            <h2>Historical Context — RAG Agent</h2>
            {ragContext === "loading" ? (
              <div className="rag-loading">
                <span className="pulse-dot" /> Checking historical incidents…
              </div>
            ) : (
              <>
                {ragContext.warning ? (
                  <p className="rag-warning">{ragContext.warning}</p>
                ) : (
                  <p className="rag-empty">
                    {ragContext.error || "No generated warning available for this alert."}
                  </p>
                )}
                {ragContext.sources && ragContext.sources.length > 0 && (
                  <div className="rag-sources">
                    <div className="rag-sources-label">Retrieved from:</div>
                    <ul>
                      {ragContext.sources.map((s, i) => (
                        <li key={i}>
                          <span className="rag-source-file">{s.source}</span>
                          <span className="rag-source-excerpt">{s.excerpt.slice(0, 140)}…</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            )}
          </section>
        )}
      </main>
    </div>
  );
}

const css = `
:root {
  --bg: #14171c;
  --panel: #1b1f27;
  --grid: #2a2f3a;
  --text: #e8eaed;
  --muted: #8b93a1;
  --safe: #2dd4a7;
  --caution: #ffb020;
  --danger: #ff3b30;
}
* { box-sizing: border-box; }
html, body {
  margin: 0 !important;
  padding: 0 !important;
  width: 100%;
  height: 100%;
}
#root {
  max-width: none !important;
  width: 100% !important;
  margin: 0 !important;
  padding: 0 !important;
  text-align: left !important;
}
.app {
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  width: 100%;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 24px;
  border-bottom: 1px solid var(--grid);
}
.brand {
  font-family: "Arial Narrow", sans-serif;
  font-weight: 700;
  letter-spacing: 2px;
  font-size: 20px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.brand-mark {
  width: 10px; height: 10px;
  background: var(--caution);
  display: inline-block;
  transform: rotate(45deg);
}
.brand-sub { color: var(--muted); font-weight: 400; }
.topbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
  font-family: "Courier New", monospace;
  font-size: 13px;
  color: var(--muted);
}
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot-live { background: var(--safe); box-shadow: 0 0 8px var(--safe); }
.dot-complete { background: var(--caution); box-shadow: 0 0 8px var(--caution); }
.dot-offline { background: var(--muted); }
.btn {
  background: var(--caution);
  color: #14171c;
  border: none;
  padding: 8px 16px;
  font-weight: 700;
  letter-spacing: 1px;
  font-size: 12px;
  cursor: pointer;
  border-radius: 3px;
}
.btn:disabled { opacity: 0.6; cursor: default; }
.alert-banner {
  position: relative;
  overflow: hidden;
  padding: 14px 24px;
  color: #14171c;
  font-weight: 700;
}
.hazard-stripes {
  position: absolute; inset: 0;
  background: repeating-linear-gradient(135deg, var(--caution) 0 20px, #14171c 20px 40px);
  opacity: 0.9;
}
.alert-content {
  position: relative;
  background: rgba(20,23,28,0.0);
  color: #14171c;
  text-shadow: 0 1px 0 rgba(255,255,255,0.3);
}
.grid {
  display: grid;
  grid-template-columns: 2fr 1fr;
  grid-template-rows: auto auto;
  gap: 16px;
  padding: 24px;
}
.panel {
  background: var(--panel);
  border: 1px solid var(--grid);
  border-radius: 6px;
  padding: 16px;
}
.panel h2 {
  font-family: "Courier New", monospace;
  font-size: 12px;
  letter-spacing: 1.5px;
  color: var(--muted);
  text-transform: uppercase;
  margin: 0 0 12px;
}
.zones { grid-column: 1; grid-row: 1; }
.gauge-panel { grid-column: 2; grid-row: 1 / span 2; }
.log { grid-column: 1; grid-row: 2; }
.rag { grid-column: 1; grid-row: 3; }
.cv-panel { grid-column: 2; grid-row: 3; }
.video-wrap { position: relative; width: 100%; aspect-ratio: 16 / 9; border-radius: 4px; overflow: hidden; background: #000; }
.cv-video { width: 100%; height: 100%; object-fit: cover; display: block; }
.cv-video-error {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  color: var(--muted); font-size: 12px; font-family: "Courier New", monospace; text-align: center; padding: 16px;
}
.cv-badge {
  position: absolute; top: 10px; left: 10px;
  padding: 6px 12px; border-radius: 4px;
  font-family: "Courier New", monospace; font-size: 12px; font-weight: 700;
  letter-spacing: 0.5px;
}
.cv-badge-present { background: var(--caution); color: #14171c; }
.cv-badge-violation { background: var(--danger); color: #fff; }
.zone-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 10px;
}
.zone-tile {
  border: 1px solid var(--tile-color);
  border-radius: 4px;
  padding: 12px 8px;
  text-align: center;
  transition: border-color 0.3s;
}
.zone-name { font-family: "Courier New", monospace; color: var(--muted); font-size: 12px; }
.zone-score { font-size: 26px; font-weight: 700; color: var(--tile-color); font-family: "Courier New", monospace; }
.zone-gas { font-size: 11px; color: var(--muted); font-family: "Courier New", monospace; }
.gauge { text-align: center; }
.gauge-reading { margin-top: -30px; }
.gauge-value { font-family: "Courier New", monospace; font-size: 32px; font-weight: 700; }
.gauge-unit { display: block; font-size: 11px; color: var(--muted); font-family: "Courier New", monospace; }
.log-empty { color: var(--muted); font-size: 13px; }
.log ul { list-style: none; margin: 0; padding: 0; }
.log li {
  display: flex; gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--grid);
  font-size: 13px;
  font-family: "Courier New", monospace;
}
.log-time { color: var(--muted); }
.log-zone { color: var(--caution); font-weight: 700; }
.log-reason { color: var(--text); }
.rag-loading {
  display: flex; align-items: center; gap: 8px;
  color: var(--muted); font-size: 13px; font-family: "Courier New", monospace;
}
.pulse-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--caution);
  animation: pulse 1s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.rag-warning {
  font-size: 14px; line-height: 1.6; color: var(--text);
  margin: 0 0 14px; padding: 12px; border-left: 3px solid var(--caution);
  background: rgba(255,176,32,0.06);
}
.rag-empty { color: var(--muted); font-size: 13px; margin: 0 0 14px; }
.rag-sources-label {
  font-size: 11px; color: var(--muted); letter-spacing: 1px;
  text-transform: uppercase; margin-bottom: 8px; font-family: "Courier New", monospace;
}
.rag-sources ul { list-style: none; margin: 0; padding: 0; }
.rag-sources li {
  padding: 8px 0; border-bottom: 1px solid var(--grid); font-size: 12px;
}
.rag-source-file {
  display: block; color: var(--caution); font-weight: 700;
  font-family: "Courier New", monospace; margin-bottom: 2px;
}
.rag-source-excerpt { color: var(--muted); line-height: 1.4; }
`;
