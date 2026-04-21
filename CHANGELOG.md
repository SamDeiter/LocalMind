<!-- markdownlint-disable MD024 -->
# Changelog

All notable changes to LocalMind are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to semantic versioning.

## [Unreleased]

### Added

- Placeholder for in-progress work on the next release.

### Changed

- _(nothing yet)_

### Fixed

- _(nothing yet)_

## [1.0.0] - Ignition - 2026-04-20

### Added

- Phase H hardening release — full v1.0.0 "Ignition" cut.
- Autonomous Agent Mode (Phase F): sourced reporting, goal planning, and
  background gap-analysis execution (`backend/autonomy/`, `backend/routes/autonomy.py`).
- Report Forge (Phase F2): MedGemma-powered school-interview note summarization
  with immutable SHA-256 hashes.
- VS Code Extension (Phase E): sidebar WebView panel and Context Extractor
  hook for TypeScript / VS Code API projects.
- Intelligence Map visualization and cryptographic skill integrity checks
  (Phase G): `backend/security/integrity.py`, `backend/routes/intelligence.py`,
  `frontend/modules/intelligence_map_ui.js`.
- Google Workspace OAuth (Phase D6): persistent local token store for Drive,
  Docs, Gmail, Sheets, Slides, and Calendar.
- Streaming resilience (Phase D5): `TokenCheckpoint` buffers Ollama chunks and
  preserves session memory across disconnects.
- Multi-model routing: `medgemma:4b`, `gemma4:e2b`, `gemma4:e4b`, `gemma4:27b`,
  and `qwen3` / `deepseek-r1` tiers.
- Cross-Project Hub (Phase D): `/api/hub/context` endpoint and tech-stack
  scanner for sibling repos.
- LOCALMIND_SSOT.md as the single source of truth for v1.0.0 scope.

### Changed

- Model router retuned for 10 GB VRAM; removed 11 unused models (~70 GB freed).
- Frontend rebuilt around the 3-tab shell (Phases 1-5) with shared card
  components and loading skeletons.
- Dashboard redesign plus typography and sidebar grouping overhaul.

### Fixed

- Parser now handles unwrapped tool calls so LLM-generated `pptx` invocations
  execute correctly.
- Tool calling, conversation memory, and thinking-token filtering regressions.
- RAG quality, broken chat, jobs, and PowerPoint support.
- Frontend API port restored to 8001 to match `LocalMind.bat`.
- Clean search queries now extracted from raw user messages.

## [0.9.1] - 2026-04-20

### Added

- Phase 9 Intelligence groundwork: RCA wired into metacognition, git-awareness
  tools, and the cross-project hub scaffolding.
- MemPalace full three-tier memory integration (Tier-1 working, Tier-2 episodic,
  Tier-3 long-term).
- Job deletion for completed, failed, and cancelled jobs in the Jobs view.
- Sprint 8 "Digital Life" — AI self-discovery and internet learning loops.
- Sprint 7 "Perception & Memory" — token visualization, encrypted onboarding,
  dual-memory surfaces, cloud brain.
- Sprint 6 — self-extending tools, TTS, eval harness, PWA packaging, and the
  VS Code extension skeleton.

### Changed

- Model tiers retuned for 10 GB VRAM; upgraded defaults to `qwen3` /
  `deepseek-r1`.
- Job pipeline overhaul with dashboard rebuild and LLM-client improvements.
- UI/UX polish pass: reduced notification noise, fixed view bugs, unified
  components, typography and sidebar grouping.
- Phase 4 cleanup — removed stale element refs and fixed `switchSwarmTab`
  selector.

### Fixed

- Tool-calling pipeline: executor fallback and working web-search path.
- Template parroting suppressed in tool-call output.
- Frontend API port restored to 8001 to match `LocalMind.bat`.
- Broken chat, jobs, RAG quality, and PowerPoint support restored.
- Autonomy engine self-editing pipeline removed (kept the learning system).
- Removed ~4,500 lines of dead code (orphaned CSS + legacy scripts).

[Unreleased]: https://github.com/SamDeiter/LocalMind/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/SamDeiter/LocalMind/releases/tag/v1.0.0
[0.9.1]: https://github.com/SamDeiter/LocalMind/releases/tag/v0.9.1
