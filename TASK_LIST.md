# LocalMind — Technical Action Plan (v0.7.0 -> v0.8.0)

This document outlines the detailed roadmap for addressing technical debt, UI/UX improvements, and next-generation intelligence features, moving LocalMind from its current state toward fully autonomous reliability.

## 🎯 Phase A: Architectural Refactoring & Stability (Immediate)
*Goal: Reduce tech debt, stabilize the module system, and improve maintainability.*

- [x] **A1. Engine Modularization (`backend/autonomy/engine.py`)**
  - Break down the monolithic `AutonomyEngine` class (currently doing too much).
  - Extract Reflection & Proposal logic to `reflection_service.py`.
  - Extract Git/State awareness to `git_coordinator.py`.
  - Keep `engine.py` tightly focused on pure orchestration.
- [x] **A2. UI Globals Resolution (`frontend/modules/`)**
  - Review all ESLint conflicts regarding undefined globals (e.g., `Prism`, `brainPulse`, `marked`).
  - Create a safe `window.LocalMind` shared namespace or explicit module imports to eliminate errors and prevent race conditions.
- [x] **A3. Configuration Finalization**
  - complete the migration of any remaining hardcoded Ollama URLs to respect the `.env` `OLLAMA_BASE_URL` standard.

## 🎨 Phase B: UI/UX & Product Polish (Short-term)
*Goal: Remove friction for new users and surface deeper analytical insights.*

- [x] **B1. Interactive Onboarding Tutorial** — DONE in Phase 8
  - Build a lightweight, CSS-driven "2-minute tour" for first-time launch.
  - Highlight key areas: The Action Stream overview, Monaco Editor integration, and Model Selection capabilities.
- [x] **B2. Enhanced Daily Digest UI** — DONE in Phase 8
  - Enhance the digest payload with deep metrics (Tokens processed, Models utilized, total execution duration).
  - Render an "Autonomous Value" summary showing total time saved and edit success rates.

## 🧠 Phase C: Next-Gen Intelligence (Medium-to-Long-term)
*Goal: Fulfill the definitive LocalMind vision — offline voice and total version control.*

- [x] **C1. Piper TTS Offline Voice Integration** — DONE in Phase 9 Sprint 6
  - Integrate Piper for hyper-fast, offline neural voice synthesis.
  - Build frontend controls to enable/disable TTS for the AI's internal reasoning loop, completing the "JARVIS" feel.
- [x] **C2. AI Time Machine (Action Replay Architecture)** — DONE in Phase 9 Sprint 4
  - Implement a secondary database or structured log for every distinct code modification the AI makes.
  - Build a "Timeline Slider" UI allowing the user to scrub forward/backward through AI edits and 1-click restore/rollback previous file states.
