# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2026-07-07 - [Accessibility and Icon Consistency]
**Learning:** Replacing emojis with standard Material Symbols improves visual consistency and styling control. Managing focus during asynchronous state transitions (e.g., shifting focus to a '#stopBtn' when streaming begins) is a vital micro-UX improvement that ensures keyboard users can immediately interact with new, time-sensitive controls.
**Action:** When toggling the visibility of interactive elements during async operations, always manage the document focus to keep the interface accessible and intuitive.
