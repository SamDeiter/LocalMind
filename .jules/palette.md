# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-20 - [Conversation List Accessibility & Feedback]
**Learning:** Interactive elements hidden behind hover states (like "Delete" or "Export" buttons in a list) are inaccessible to keyboard users unless they are also triggered by focus. Using Tailwind's `group-focus-within:opacity-100` on the container ensures these actions become visible when any element inside them gains focus. Additionally, destructive actions MUST have a confirmation step, and all async operations should provide observable success/error feedback via toasts to maintain user confidence.
**Action:** Always use `group-focus-within` for hover-actions and provide explicit feedback for all sidebar interactions.
