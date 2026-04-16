# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-16 - [Context-Aware Interaction & Accessibility Guardrails]
**Learning:** UX improvements should scale with the complexity of the task. While simple textareas benefit from auto-resizing, multi-step pipelines require confirmation dialogs when deleting content to prevent accidental data loss. Accessibility for custom interactive elements (like drop zones) must include explicit keyboard listeners for `Enter` and `Space` alongside appropriate ARIA roles and tab indices to ensure a fully inclusive experience.
**Action:** Pair visual enhancements with safety confirmations for destructive actions and comprehensive keyboard support for custom UI components.
