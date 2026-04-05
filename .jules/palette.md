# Palette's Journal - Critical UX/Accessibility Learnings

## 2026-04-05 - Visual Feedback for Async Actions
**Learning:** Users need immediate visual feedback when an action (like sending a message) is in progress, beyond just disabling the button. Adding `opacity-50` and `cursor-not-allowed` makes it clear that the interface is busy.
**Action:** Always combine `disabled` state with visual classes like `opacity-50` and `cursor-not-allowed` for primary action buttons.
