# SafeNet — A Multi-Agent AI Brain for Zero-Harm Industrial Operations

SafeNet predicts industrial accidents before they happen by cross-referencing
sensor data, work permits, and CCTV feeds in real time — instead of monitoring
each in isolation.

## Architecture

Four layers, one-directional data flow:

1. **Data sources** — sensor feed simulator, permit logs, CCTV feed, incident docs
2. **Processing modules** — risk engine, CV module (YOLOv11n), RAG agent
3. **Backend** — FastAPI orchestrator with WebSocket streaming
4. **Frontend** — React dashboard (live alerts, risk gauges, zone map)

## Repo structure

```
backend/       FastAPI app, risk engine, sensor simulator
cv-module/     YOLOv11n training + inference for worker/PPE detection
rag-agent/     LangChain + ChromaDB + Gemini incident pattern search
frontend/      React dashboard
data/          Simulated sensor data, incident reports, OISD docs
docs/          Architecture diagrams, presentation assets
```

## Setup

_To be filled in as each module comes online._

## Team

- [Vodnala Srinidhi] — CV pipeline, RAG agent, architecture diagram, presentation deck
- [Vaduguru Jashwanth Sai] — Sensor simulator, risk engine, React frontend, backend, demo video
