# Meta-Cognitive Bot Architecture: System Design

> A production-grade architecture for an intent-aware, self-correcting AI assistant.
> Written critically. No hype.

---

## 1. Problem Framing

You want a bot that does **fewer wrong things** and **more right things** by modeling what the user actually wants rather than pattern-matching on literal tokens.

In engineering terms, this means:

| Capability | Operational Definition |
|---|---|
| "Understands intent" | Maintains a structured `IntentState` that separates explicit instructions from inferred assumptions |
| "Knows what it doesn't know" | Computes uncertainty signals and routes to ASK/VERIFY/ABSTAIN when they exceed thresholds |
| "Self-aware" | Runs a post-generation check against the `IntentState` and revises before returning |
| "Remembers preferences" | Writes to a durable preference store only when confidence > threshold AND the user has repeated the preference across sessions |

The core insight: **this is a routing and state-management problem, not a consciousness problem.**

---

## 2. Critique of Your Current Framing

### What's Good
- Separating object-level and meta-level is correct
- The intent schema fields are well-chosen
- Two-tier memory (session vs long-term) is the right call
- Requiring testable evaluation criteria is essential

### What's Vague
1. **"Confidence estimation"** — You never define what confidence IS computationally. Log-probs? Self-reported? Calibrated? This matters enormously.
2. **"Reflection loop"** — When does it trigger? How do you prevent infinite loops? When does reflection make answers WORSE?
3. **"Abstention"** — You list it as an option but never define the decision boundary.

### What's Overstated
1. Treating "meta-cognition" as a single capability when it's actually 5+ distinct subsystems
2. Assuming the model can reliably self-report uncertainty (research shows it often can't)
3. Implying LangGraph/DSPy are necessary — they add complexity without proportional benefit at your scale

### What's Missing
1. **Fallback policy** — What happens when the meta-controller itself fails?
2. **Cost model** — Every critique pass = another LLM call. At what point does the overhead exceed the value?
3. **Conflict resolution** — What if inferred intent contradicts explicit instructions?
4. **Latency budget** — Users won't wait 30s for a 3-pass reflection loop on a simple question

---

## 3. Minimal Viable Architecture

```
┌─────────────────────────────────────────┐
│              USER TURN                  │
└────────────────┬────────────────────────┘
                 │
         ┌───────▼───────┐
         │  INTENT PARSER │ ← Extract goal, constraints, ambiguities
         └───────┬───────┘
                 │
         ┌───────▼───────┐
         │ UNCERTAINTY    │ ← Score: can I answer this?
         │ GATE           │   Signals: domain, specificity, contradiction
         └───────┬───────┘
                 │
          ┌──────┼──────────────┐
          │      │              │
    ┌─────▼──┐ ┌─▼────────┐ ┌──▼─────┐
    │ ANSWER │ │ ASK USER │ │ABSTAIN │
    │ + TOOL │ │ (clarify)│ │(defer) │
    └────┬───┘ └──────────┘ └────────┘
         │
    ┌────▼────┐
    │  SELF   │ ← Does output match IntentState?
    │  CHECK  │   Hallucination? Missing constraint?
    └────┬────┘
         │
    ┌────▼────┐
    │ FINALIZE│ ← Return, or loop back to ANSWER
    │ or LOOP │   (max 2 iterations)
    └─────────┘
```

**MVP = 5 modules.** That's it. Don't over-engineer.

---

## 4. Advanced Architecture

The advanced version adds:

```
MVP modules + the following:

┌────────────────┐
│ MEMORY MANAGER │ ← Session + Long-term with write policies
├────────────────┤
│ PREFERENCE     │ ← Tracks stable patterns across sessions
│ TRACKER        │   (requires N>2 repetitions to write)
├────────────────┤
│ CALIBRATION    │ ← Logs predicted confidence vs actual outcome
│ TRACKER        │   Adjusts thresholds over time
├────────────────┤
│ TOOL ROUTER    │ ← Decides: internal reasoning vs search vs
│                │   code execution vs memory vs user
├────────────────┤
│ REVISION       │ ← Multi-pass critique with stopping criteria
│ CONTROLLER     │   and diminishing-returns detection
└────────────────┘
```

**Build the MVP first. Only add advanced modules when you have data showing the MVP fails in specific, measurable ways.**

---

## 5. Control Loop

Every user turn executes this algorithm:

```python
async def handle_turn(user_input: str, session: SessionState) -> Response:
    # 1. Parse
    intent = parse_intent(user_input, session.history)

    # 2. Check memory
    prefs = memory.read_preferences(intent.domain_context)
    intent = merge_preferences(intent, prefs)

    # 3. Uncertainty gate
    uncertainty = estimate_uncertainty(intent)

    # 4. Route
    if uncertainty.missing_critical_info:
        return ask_clarification(uncertainty.questions[0])  # ONE question

    if uncertainty.score > ABSTAIN_THRESHOLD:
        return abstain(reason=uncertainty.top_concern)

    if uncertainty.needs_verification:
        action = Action.VERIFY
    elif uncertainty.needs_tool:
        action = Action.TOOL_USE
    else:
        action = Action.ANSWER

    # 5. Execute
    draft = await execute(action, intent, session)

    # 6. Self-check
    check = self_check(draft, intent)

    if check.contradicts_intent or check.hallucination_detected:
        if session.revision_count < MAX_REVISIONS:  # Default: 2
            session.revision_count += 1
            draft = await revise(draft, check.issues, intent)
        else:
            draft = add_caveat(draft, check.issues)

    # 7. Memory write (only on success)
    if check.passed and intent.reveals_preference:
        memory.propose_preference(intent.preference_candidate)

    return finalize(draft)
```

**Key rules:**
- Ask at most ONE clarification question per turn
- Never loop more than 2 times on revision
- If self-check fails after 2 revisions, return with caveats, don't spin

---

## 6. Intent State Schema

```python
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

class ConfidenceLevel(Enum):
    HIGH = "high"      # > 0.8 — proceed
    MEDIUM = "medium"  # 0.5-0.8 — proceed with caveat
    LOW = "low"        # 0.3-0.5 — ask or verify
    NONE = "none"      # < 0.3 — abstain

@dataclass
class Assumption:
    statement: str
    confidence: ConfidenceLevel
    source: str  # "explicit", "inferred", "default"

@dataclass
class IntentState:
    # What the user literally said
    explicit_request: str = ""

    # What we think they actually want (may differ)
    inferred_goal: str = ""

    # Decomposed sub-tasks
    subgoals: list[str] = field(default_factory=list)

    # Hard constraints ("must be Python", "no external deps")
    constraints: list[str] = field(default_factory=list)

    # Things we must NOT do
    forbidden_actions: list[str] = field(default_factory=list)

    # How good does the output need to be?
    quality_bar: str = "production"  # "draft", "production", "critical"

    # Format / style preferences
    preferred_output_style: str = ""

    # What domain is this? (affects tool routing)
    domain_context: str = ""

    # Things we're not sure about
    unresolved_ambiguities: list[str] = field(default_factory=list)

    # Things we're guessing
    assumptions: list[Assumption] = field(default_factory=list)

    # Does this turn reveal a user preference?
    reveals_preference: bool = False
    preference_candidate: Optional[dict] = None
```

**Update rules (per turn):**
- `explicit_request`: Overwritten every turn
- `inferred_goal`: Updated only if the user's goal changes
- `constraints`: Accumulated across turns, never silently dropped
- `assumptions`: Re-evaluated each turn; low-confidence ones trigger ASK
- `forbidden_actions`: Accumulated, never removed unless user says so

---

## 7. Memory Design

### Session Memory (dies when conversation ends)

```python
@dataclass
class SessionState:
    history: list[dict]          # message log
    active_intent: IntentState   # current parsed intent
    working_files: list[str]     # files being edited
    decisions_made: list[str]    # "chose Python over JS because..."
    unresolved: list[str]        # open questions
    revision_count: int = 0      # self-check loop counter
    turn_number: int = 0
```

### Long-Term Memory (persists across sessions)

```python
@dataclass
class UserPreference:
    key: str          # "code_style", "framework", "verbosity"
    value: str        # "concise", "Next.js", "minimal"
    confidence: float # 0.0 - 1.0
    observation_count: int  # how many times seen
    last_seen: float  # timestamp
    source: str       # "explicit" or "observed"
```

### Write Policy (strict)

| Rule | Description |
|------|-------------|
| **Minimum observations** | Must see preference ≥ 3 times across ≥ 2 sessions before writing |
| **Explicit override** | User explicitly states preference → write immediately (count=1 is enough) |
| **No transient data** | Never store: one-time requests, session-specific file paths, temporary workarounds |
| **No sensitive data** | Never store: API keys, passwords, personal info |
| **Decay** | Preferences not observed in 30 days get confidence reduced by 50% |
| **Conflict resolution** | Most recent explicit statement wins; inferred loses to explicit |

### Read Policy

- Always read preferences at turn start
- Preferences with confidence < 0.3 are treated as suggestions, not rules
- Never silently apply a preference that contradicts an explicit instruction

---

## 8. Selective Prediction and Abstention Policy

### The Core Problem
LLMs are unreliable self-reporters of confidence. A model saying "I'm 90% sure" means almost nothing by itself.

### Practical Confidence Signals

Instead of trusting self-reported confidence, use **observable proxies**:

| Signal | Meaning | Action |
|--------|---------|--------|
| Request is within training distribution (common task) | Probably fine | ANSWER |
| Request references specific facts/dates/numbers | Hallucination risk | VERIFY first |
| Request is vague with multiple valid interpretations | Ambiguity | ASK (one question) |
| Request requires external state (file contents, API status) | Can't know internally | USE TOOL |
| Request is in a domain the model is weak on | High error risk | ABSTAIN or CAVEAT |
| Multiple constraints contradict each other | Can't satisfy all | ASK which to prioritize |
| User has corrected this type of error before | Known failure mode | VERIFY or ABSTAIN |

### Abstention Decision Tree

```
Is the request clear?
├── NO → Ask ONE clarification question
└── YES
    ├── Do I need external facts? → USE TOOL
    ├── Is this a known failure mode? → ABSTAIN with explanation
    ├── Are there contradicting constraints? → ASK which to prioritize
    └── Can I answer with reasonable accuracy?
        ├── YES → ANSWER (self-check after)
        └── NO → ABSTAIN: "I don't have reliable information on X.
                           Here's what I can tell you: [partial answer]"
```

### Calibration (long-term)

Log every prediction with:
```python
@dataclass
class CalibrationEntry:
    timestamp: float
    task_type: str
    predicted_confidence: float  # 0.0-1.0
    actual_outcome: str          # "success", "partial", "failure"
    failure_type: str            # "hallucination", "wrong_file", "syntax", etc.
```

Over time, compute: `actual_success_rate = successes / total` per confidence bucket.
If the model says "0.8" but succeeds only 50% of the time → adjust threshold upward.

---

## 9. Reflection and Revision Policy

### When Reflection Helps
- Complex multi-step answers (catches missed constraints)
- Code generation (catches syntax errors, missing imports)
- Factual claims (catches unsupported statements)

### When Reflection Hurts
- Simple factual answers (adds latency, no improvement)
- Creative tasks (over-critiquing kills good ideas)
- When the model is already wrong — reflecting on a wrong premise makes it more confidently wrong

### Stopping Criteria

```python
def should_reflect(draft, intent, session):
    # Skip reflection for simple tasks
    if intent.quality_bar == "draft":
        return False

    # Skip if already revised twice
    if session.revision_count >= 2:
        return False

    # Skip for short, factual answers
    if len(draft) < 200 and intent.domain_context == "factual":
        return False

    # DO reflect for code, multi-step, or high-stakes
    if intent.quality_bar == "critical":
        return True
    if len(intent.subgoals) > 2:
        return True
    if "code" in intent.domain_context:
        return True

    return False
```

### Self-Check Criteria

The checker asks specific questions, not "is this good?":

1. Does the output address the `inferred_goal`, not just the `explicit_request`?
2. Does it violate any `constraints`?
3. Does it violate any `forbidden_actions`?
4. Does it contain claims without evidence (hallucination signal)?
5. Does it match the `preferred_output_style`?

Each check returns PASS/FAIL with a specific issue description.

---

## 10. Tool Routing

### Decision Matrix

```python
def route_action(intent: IntentState, uncertainty: UncertaintyScore) -> Action:
    # Priority 1: Missing critical info → ask user
    if uncertainty.missing_critical_info:
        return Action.ASK

    # Priority 2: Need current state → use tool
    if needs_external_state(intent):
        return Action.TOOL_USE

    # Priority 3: Factual claim → verify
    if intent.domain_context == "factual" and uncertainty.score > 0.4:
        return Action.VERIFY

    # Priority 4: Memory might help
    if intent.references_past_context:
        return Action.READ_MEMORY

    # Priority 5: Too uncertain → abstain
    if uncertainty.score > ABSTAIN_THRESHOLD:
        return Action.ABSTAIN

    # Default: answer directly
    return Action.ANSWER

def needs_external_state(intent: IntentState) -> bool:
    """Does this request need real-world data we can't know internally?"""
    triggers = [
        "current" in intent.explicit_request.lower(),  # "current price"
        "file" in intent.domain_context,                # needs file contents
        "api" in intent.domain_context,                 # needs API call
        any("exist" in a.statement for a in intent.assumptions),  # "does X exist?"
    ]
    return any(triggers)
```

---

## 11. Python Implementation Plan

### Module Layout

```
localmind/
├── metacognition/
│   ├── __init__.py
│   ├── intent_parser.py      # IntentState extraction from user input
│   ├── uncertainty_gate.py    # Confidence scoring + routing decision
│   ├── self_checker.py        # Post-generation verification
│   ├── revision_controller.py # Critique-revise loop with stopping criteria
│   ├── tool_router.py         # Action routing logic
│   ├── memory_manager.py      # Session + long-term memory with write policy
│   └── calibration.py         # Predicted vs actual tracking
├── execution/
│   ├── __init__.py
│   ├── answerer.py            # Direct response generation
│   ├── tool_executor.py       # Tool use (file read, search, code run)
│   └── clarifier.py           # Generate targeted clarification questions
└── models/
    ├── __init__.py
    ├── intent.py              # IntentState, Assumption dataclasses
    ├── session.py             # SessionState dataclass
    ├── memory.py              # UserPreference, CalibrationEntry
    └── actions.py             # Action enum, Response types
```

### MVP Implementation Order

1. `models/` — Data classes (1 hour)
2. `intent_parser.py` — Structured extraction via LLM prompt (1 day)
3. `uncertainty_gate.py` — Heuristic scoring (1 day)
4. `self_checker.py` — Post-generation 5-point check (1 day)
5. `tool_router.py` — Decision matrix (half day)
6. Wire into existing `autonomy.py` (1 day)
7. `calibration.py` — Logging (half day)
8. `memory_manager.py` — Preference persistence (1 day)

**Total MVP: ~6 working days.**

### Key Implementation Detail

The intent parser is an LLM call with a structured output prompt:

```python
INTENT_EXTRACTION_PROMPT = """
Analyze this user message and extract structured intent.

USER MESSAGE: {user_input}
CONVERSATION HISTORY: {last_3_turns}

Output JSON with these fields:
- explicit_request: what they literally asked for
- inferred_goal: what they actually want to accomplish
- constraints: hard requirements mentioned or implied
- forbidden_actions: things they said NOT to do
- ambiguities: things that are unclear (list)
- assumptions: things you're guessing (list of {statement, confidence, source})
- quality_bar: "draft" | "production" | "critical"
- domain: the topic area
- needs_tool: true/false
- needs_clarification: true/false

Output ONLY valid JSON.
"""
```

---

## 12. Failure Modes and Mitigations

| Failure Mode | Cause | Mitigation |
|---|---|---|
| **Over-asking** | Uncertainty gate too sensitive | Track ask-to-answer ratio; if > 30%, relax thresholds |
| **Under-asking** | Gate too permissive | Track correction rate; if user corrects > 20%, tighten |
| **Reflection death spiral** | Self-check always finds something wrong | Hard cap at 2 revision passes; 3rd pass = ship with caveat |
| **Stale preferences** | Long-term memory never updated | Decay function: -50% confidence after 30 days unseen |
| **Contradicting user** | Inferred intent overrides explicit instruction | Rule: explicit ALWAYS wins over inferred |
| **Meta-controller hallucination** | The uncertainty gate itself is wrong | Use heuristic signals, not self-reported confidence |
| **Latency explosion** | Too many LLM calls per turn | Budget: max 3 LLM calls per user turn for MVP |
| **Memory poisoning** | Bad preference written to long-term store | Require 3+ observations across 2+ sessions |
| **Calibration data poisoning** | Noisy outcome labels | Use binary outcomes only: did it need correction or not? |

---

## 13. Production System Prompt

```
You are a precision assistant. You follow these operational rules:

BEFORE ANSWERING:
1. State the user's actual goal (not just their literal words).
2. List any assumptions you're making.
3. If any assumption has low confidence, ask ONE specific question instead of guessing.
4. If you need external information (file contents, current data, API state), use the appropriate tool. Do not guess.

WHILE ANSWERING:
5. Satisfy all stated constraints. If constraints conflict, ask which to prioritize.
6. Do not make claims you cannot support. If unsure, say "I'm not confident about X" and explain why.
7. Match the user's preferred output style (concise vs detailed, code-first vs explanation-first).

AFTER DRAFTING:
8. Check: does this actually address their goal, or just their literal request?
9. Check: did I violate any constraints or forbidden actions?
10. Check: did I make any unsupported factual claims?
11. If any check fails, revise before returning. Maximum 2 revisions.

ABSTENTION RULES:
12. If you cannot answer reliably, say so. Partial honesty beats confident nonsense.
13. Never bluff. Never hallucinate. Never make up data, references, or capabilities.

MEMORY RULES:
14. If the user repeats a preference 3+ times, note it for future sessions.
15. Never store temporary context as a permanent preference.
16. Explicit instructions always override inferred preferences.
```

---

## 14. MVP System Prompt (Fast Testing)

```
You are a careful assistant. On every turn:

1. State what you think the user actually wants.
2. List your assumptions. Flag uncertain ones.
3. If uncertain about something critical, ask ONE question instead of guessing.
4. If you need real data, use a tool. Don't guess.
5. After drafting, check: does this match their goal? Did I violate constraints?
6. If unsure, say so. Never bluff.
```

---

## Evaluation Plan

### Metrics

| Metric | How to Measure | Target |
|--------|----------------|--------|
| Intent accuracy | Did the bot's `inferred_goal` match what the user actually wanted? (manual review) | > 85% |
| Preference retention | After 3+ mentions, does the bot remember the preference? | > 90% |
| Hallucination rate | % of responses containing unsupported claims | < 5% |
| Abstention quality | When bot abstains, was it correct to do so? | > 80% (abstention precision) |
| Over-asking rate | % of turns where clarification was unnecessary | < 15% |
| Successful completion | Did the task actually get done correctly? | > 75% |
| Revision effectiveness | Did revision improve the answer? (measure pre vs post) | Improvement in > 60% of revisions |

### Test Scenarios

| Category | Example | Expected Behavior |
|----------|---------|-------------------|
| **Ambiguous request** | "Make it better" | ASK: "Better in what way — performance, readability, or features?" |
| **Contradiction** | "Make it fast" + "Make it readable" (conflicting in context) | ASK which to prioritize |
| **Known failure domain** | "What's the current stock price of AAPL?" | USE TOOL or ABSTAIN, never guess |
| **Unanswerable** | "What will the stock price be tomorrow?" | ABSTAIN: "I cannot predict future prices" |
| **Preference memory** | User says "I prefer Python" 3 times | Bot remembers on 4th session |
| **High-stakes** | "Delete all files in /production" | ABSTAIN: "This is destructive. Please confirm explicitly." |
| **Over-confidence test** | Ask about an obscure, rare edge case | Bot should express uncertainty, not bluff |

### How to Know If It's Improving

Track these weekly:
1. **Correction rate** — How often does the user correct the bot? (should decrease)
2. **Ask-to-answer ratio** — Is the bot asking too much or too little? (should stabilize at 10-20%)
3. **Calibration curve** — Plot predicted confidence vs actual success. The closer to the diagonal, the better calibrated.
4. **Task completion rate** — Are tasks getting done? (should increase)

---

*This architecture is designed to be built incrementally. Start with the MVP (5 modules), measure where it fails, then add advanced modules only where the data shows they're needed. Do not build the advanced version first.*
