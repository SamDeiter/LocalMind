# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2026-05-23 - [Keyboard Discoverability for Hover Actions]
**Learning:** Actions that only appear on hover (e.g., delete/export buttons in a list) are invisible to keyboard users. Using `group-focus-within:opacity-100` alongside hover triggers ensures these actions become visible when any element within the group receives focus, making the interface fully navigable by keyboard without cluttering the default view.
**Action:** Always pair hover-based visibility with focus-aware CSS triggers for secondary action buttons.
