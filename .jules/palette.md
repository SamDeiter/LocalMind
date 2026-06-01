# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-20 - [Accessibility & Safety Improvements for Conversation Management]
**Learning:** Nested interactive elements (like placing buttons inside a `role="button"` container) cause significant accessibility issues and unpredictable screen reader behavior. Refactoring to separate title buttons from action buttons ensures valid HTML and a better tab order. Additionally, confirmation dialogs for destructive actions like 'Delete' are essential to prevent accidental data loss in production environments.
**Action:** Use separate `<button>` elements for all interactive actions in list items and always implement `confirm()` for destructive operations.
