# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-22 - [Keyboard-Accessible Sidebar Actions]
**Learning:** Using `group-hover` to hide action buttons until hover creates "keyboard traps" or invisible interactive elements for non-mouse users. Adding `group-focus-within` ensures that these essential actions (like Export and Delete) are visible and discoverable for users navigating with Tab or screen readers.
**Action:** Always pair `group-hover` with `group-focus-within` for contextual actions to maintain full keyboard accessibility.
