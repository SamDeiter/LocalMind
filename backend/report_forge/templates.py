"""
templates.py - Few-shot formatting guides (Phase F2)
====================================================
Loads example reports as formatting guides for MedGemma.

Each example is a dict with:
  - input:  raw, messy interview/observation notes (as a case manager might jot down)
  - output: the cleaned, structured report we want MedGemma to produce
  - label:  short human tag (for debugging / audit only — not sent to model)

The formatter builds a chat prompt as:
    system → (user=ex.input, assistant=ex.output) × N → user=real_input

TEMPLATE_VERSION is stored in the audit log so we can diff reports whose
prompt semantics changed.
"""

TEMPLATE_VERSION = "1.0"

# --- Example 1: legacy seed (kept intact per spec) ------------------------------
_EXAMPLE_MATH_QUICK_NOTE = {
    "label": "math-quick-note",
    "input": "Student had trouble with math today.",
    "output": "# Math Assessment\nObservation: Difficulty with quantitative reasoning.",
}

# --- Example 2: clinical/evaluation language -----------------------------------
_EXAMPLE_CLINICAL_EVAL = {
    "label": "clinical-eval-wisc",
    "input": (
        "Met w/ J. M., 9yo, 4th grade, re-eval for SLD. Mom present. "
        "WISC-V administered last week — FSIQ 92, VCI 88, VSI 104, FRI 85, WMI 79, PSI 96. "
        "Working memory clearly weak, shows up in class too — teacher says he loses multi-step "
        "directions, forgets what he was doing mid-task. Reading: WJ-IV Letter-Word ID 84, "
        "Passage Comp 81. Math: Calc 95, Applied Problems 78 (word problems kill him — reading "
        "load + WM). No behavior concerns. Mom wants resource support for reading and math word "
        "problems, push-in not pull-out if possible. Annual review 5/14. Follow up: draft IEP "
        "goals for decoding fluency and WM compensatory strategies."
    ),
    "output": (
        "# Special Education Re-Evaluation Summary\n\n"
        "**Student:** J. M. (9 y.o., Grade 4)\n"
        "**Meeting Type:** SLD re-evaluation review\n"
        "**Attendees:** Case manager, Parent (mother)\n\n"
        "## Cognitive Profile (WISC-V)\n"
        "- FSIQ: 92 (Average)\n"
        "- Verbal Comprehension: 88 | Visual Spatial: 104 | Fluid Reasoning: 85\n"
        "- **Working Memory: 79 (Low Average) — area of concern**\n"
        "- Processing Speed: 96\n\n"
        "## Academic Achievement (WJ-IV)\n"
        "- Reading: Letter-Word ID 84, Passage Comprehension 81 (Low Average)\n"
        "- Math: Calculation 95 (Average); Applied Problems 78 (Low)\n"
        "- Discrepancy noted between math calculation and applied problems, consistent with "
        "working-memory-mediated reading load on word problems.\n\n"
        "## Classroom Observations\n"
        "- Teacher reports loss of multi-step directions and mid-task disengagement, "
        "corroborating WMI finding.\n"
        "- No behavioral or social concerns reported.\n\n"
        "## Parent Input\n"
        "- Requests continued resource support for reading decoding and math word problems.\n"
        "- Preference for push-in service delivery over pull-out.\n\n"
        "## Next Steps\n"
        "- Draft IEP goals targeting decoding fluency and working-memory compensatory strategies.\n"
        "- Annual review scheduled for 5/14."
    ),
}

# --- Example 3: behavioral observation notes -----------------------------------
_EXAMPLE_BEHAVIOR_OBS = {
    "label": "behavior-observation",
    "input": (
        "Observation of A. T. in Ms. Rivera's 2nd grade class, Tues 10:05–10:45 (literacy block). "
        "Independent reading first 10 min — A. flipped pages, did not track text, got up twice "
        "for tissues. Small group w/ teacher 10:15–10:35 — stayed seated, answered 2 of 4 "
        "comprehension questions, needed redirection 3x when peer was talking. Transition to "
        "writing — took ~4 min to start, chewed pencil, stared out window. Wrote 2 sentences in "
        "10 min. No peer conflict. Left class calm. Overall: on-task ~55% of observation window, "
        "most off-task behavior was passive (daydreaming, fidgeting) not disruptive. Consistent "
        "w/ prior teacher reports re: attention + writing initiation."
    ),
    "output": (
        "# Classroom Behavioral Observation\n\n"
        "**Student:** A. T.\n"
        "**Setting:** Ms. Rivera, Grade 2, literacy block\n"
        "**Date/Time:** Tuesday, 10:05–10:45 AM (40 min)\n"
        "**Observer:** Case manager\n\n"
        "## Activity-by-Activity Notes\n"
        "- **Independent reading (10:05–10:15):** page-flipping without text tracking; "
        "two self-initiated breaks (tissues). Task engagement: minimal.\n"
        "- **Teacher-led small group (10:15–10:35):** remained seated; answered 2 of 4 "
        "comprehension prompts; required 3 redirections when a peer was off-topic.\n"
        "- **Transition to writing (10:35–10:45):** ~4-minute initiation delay; pencil chewing, "
        "gaze out window; produced 2 sentences in 10 minutes.\n\n"
        "## Quantitative Summary\n"
        "- **On-task rate:** approximately 55% of the observation window.\n"
        "- **Off-task pattern:** predominantly passive (inattention, fidgeting); **no disruptive "
        "or peer-conflict behavior** observed.\n\n"
        "## Interpretation\n"
        "- Observation is consistent with prior teacher reports regarding sustained attention "
        "and writing-task initiation.\n"
        "- Transition points (independent work, new task start) appear to be the highest-"
        "difficulty moments.\n\n"
        "## Recommended Follow-Up\n"
        "- Consider task-initiation supports (visual start-cue, timed first-sentence prompt).\n"
        "- Monitor on-task rate across additional settings before adjusting service minutes."
    ),
}


# Public API — stable name used by formatter.py
FEW_SHOT_EXAMPLES: list[dict] = [
    _EXAMPLE_MATH_QUICK_NOTE,
    _EXAMPLE_CLINICAL_EVAL,
    _EXAMPLE_BEHAVIOR_OBS,
]

# Backwards-compat alias — older code referenced this name.
FEW_SHOT_TEMPLATES = FEW_SHOT_EXAMPLES
