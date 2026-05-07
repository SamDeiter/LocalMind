# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-24 - [Robust Async Feedback Patterns]
**Learning:** For asynchronous operations that affect persistent data (like delete or export), visual feedback should be provided for both the initiation and the outcome. Relying on implicit success (e.g., element disappearing) can be ambiguous. Success and error toasts provide explicit confirmation, while confirmation dialogs for destructive actions act as a critical friction point to prevent data loss.
**Action:** Implement success and error toast notifications for all async data operations and use standard confirmation dialogs for destructive actions.
