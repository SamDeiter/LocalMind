# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-16 - [Keyboard-Accessible Hidden Icons & Safety Confirmation]
**Learning:** Actions hidden behind hover states (like export and delete on conversation list items) are completely inaccessible to keyboard users unless explicitly exposed on focus via `group-focus-within` or `focus-within`. Additionally, performing destructive actions immediately without a user confirmation pop-up creates a frustrating experience and a high risk of accidental data loss.
**Action:** Always combine hover-based action buttons with `group-focus-within` visual styling, proper `aria-label` tags, and standard `window.confirm` modal dialogues for destructive processes.
