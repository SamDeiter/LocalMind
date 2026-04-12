# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-16 - [Generic Auto-resize & Button States]
**Learning:** Fixed-height textareas for user instructions lead to poor ergonomics and unnecessary scrollbars. Using a generic `autoResize` utility across all task-related inputs improves focus. Additionally, icon-only "Remove" buttons frequently lack `aria-label` attributes in this codebase, which is a critical accessibility gap.
**Action:** Use the enhanced `autoResize(elOrEvt)` utility for all multi-line inputs and always audit icon-only buttons for `aria-label` parity with their `title` attribute.
