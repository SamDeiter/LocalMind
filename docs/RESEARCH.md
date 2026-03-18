# 🔍 Research Queue

## OpenClaw SDK/API Investigation — 2026-03-18T16:59:56

**Status:** ✅ Researched
**Question:** Does OpenClaw have an SDK or API we can integrate with? Key concern: ensure LocalMind does NOT inherit OpenClaw's security risks (full PC access, no sandboxing).

### Findings

**OpenClaw Architecture:**
- **Gateway:** WebSocket server for message orchestration
- **Skills SDK:** Plugin system (Skills + Plugins) for extending capabilities
- **Memory:** Stores info in markdown files
- **Integrations:** GitHub, Slack, Gmail via custom Skills

**What we can learn from (NOT copy):**
- Their "Skills" plugin pattern is similar to our BaseTool system — we're already there
- Their GitHub/Slack integration approach via channels

**Security risks to AVOID:**
- ⚠️ Malicious plugins in their marketplace — we don't have a marketplace, so not a risk
- ⚠️ Publicly exposed instances — our server binds to localhost only
- ⚠️ Full PC filesystem access — we use workspace isolation
- ⚠️ No approval for actions — we have propose_action gating everything

**Verdict:** No SDK/API worth integrating. Our tool system is already more secure by design. Their Skills pattern validates our approach but their security model is exactly what we're building to avoid. We should continue with our own architecture.
