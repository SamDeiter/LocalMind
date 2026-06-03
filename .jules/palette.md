# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-16 - [Keyboard Accessible Sidebars & Semantic Controls]
**Learning:** Generic containers like `div` with click listeners are invisible to keyboard users and screen readers. Using semantic `<button>` elements for row actions and `group-focus-within` for visibility of nested icon-buttons ensures that the entire interface is navigable without a mouse. Empty states and confirmation dialogs are essential safety and orientation features.
**Action:** Refactor row-based selection lists to use semantic button triggers and ensure hover-actions are also focus-visible.
