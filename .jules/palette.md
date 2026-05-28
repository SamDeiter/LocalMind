# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-24 - [Semantic Accessibility & Keyboard Discoverability]
**Learning:** Using non-semantic elements (like `div`) for interactive components breaks keyboard navigation. Replacing them with `<button>` elements ensures proper focus management. Additionally, hover-only actions (like delete/export buttons) are inaccessible to keyboard users unless triggered by focus-aware CSS like `group-focus-within`. Semantic consistency via Material Symbols instead of emojis also enhances the professional feel and screen-reader predictability.
**Action:** Always use semantic HTML for interactions and ensure hover-based UI elements are also discoverable via keyboard focus.
