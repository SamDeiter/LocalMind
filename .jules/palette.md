# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-16 - [Active Interaction Feedback: Button Swapping]
**Learning:** Swapping the visibility of primary action buttons (like Send vs. Stop) during long-running async operations provides unambiguous feedback to the user about the application state and prevents conflicting actions.
**Action:** Use 'display: none' and empty string to swap visibility of adjacent buttons in flex containers to maintain layout stability while providing clear state feedback.
