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

## Dynamic Compression & HDR Texture Data Storage — 2026-03-18T17:38:26

**Status:** 🔍 Needs Research
**Question:** Can we dynamically compress/decompress data on the fly to make storage easier? Can data be encoded into HDR texture values for rapid GPU-accelerated load/unload? What steganographic or texture-based encoding techniques exist?

### Findings
_To be filled in during next research session_

## Partial Model Loading / Modular LoRA Hotswap — 2026-03-18T17:50:39

**Status:** 🔍 Needs Research
**Question:** Can we download only part of a model (e.g., a base model for general understanding) and then dynamically attach specialized layers (math, coding, etc.) from a more advanced model when a specific task is detected — then unload those layers when done? Is this possible with LoRA adapters, MoE architectures, or model sharding? What's the current state of the art for dynamic model composition?

### Findings
_To be filled in during next research session_

