# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2026-05-19 - [Keyboard Event Propagation in Composite Components]
**Learning:** Parent elements with custom keyboard handlers (e.g., role="button" with Enter/Space listeners) can unintentionally intercept events meant for their child interactive elements. Always verify the event target or use appropriate event delegation/stopping to ensure children remain keyboard-accessible.
**Action:** Use `if (e.target !== container) return;` in parent keyboard listeners or ensure children `stopPropagation()` for the same keys.
