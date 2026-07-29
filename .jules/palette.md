# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2026-07-26 - [Keyboard-Accessible Actions in Hover Lists]
**Learning:** List items that reveal action buttons only on hover (e.g., via `group-hover:opacity-100`) are completely inaccessible to screen readers and keyboard-only users who navigate via Tab. Adding `group-focus-within:opacity-100` alongside `group-hover` ensures that focusing on any button within the list item makes the entire actions pane visible and usable immediately.
**Action:** When designing hover-to-reveal utility buttons inside list items, always include focus-visible rings and use `group-focus-within` to ensure full keyboard discoverability.
