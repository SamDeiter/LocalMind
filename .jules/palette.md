# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-16 - [Robust Conversation Management UX]
**Learning:** Destructive actions (deletion) and background tasks (exporting) need explicit confirmation and clear success/error feedback. Using Material Symbols instead of standard emojis provides a more consistent, professional look that aligns with the system's design language, provided the font dependency is managed.
**Action:** Implement `confirm()` for destructive actions, use `showToast` for async feedback, and ensure all icon-only buttons have descriptive `aria-label` attributes.
