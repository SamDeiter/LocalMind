# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2026-07-26 - [Stream Interruption & Accessibility Controls]
**Learning:** For long-running async streaming content like LLM responses, providing users with the ability to interrupt generation is critical for accessibility and agency. A beautifully integrated "Stop" button that handles state.abortController, toggles visibility, and has an explicit, descriptive `aria-label` (e.g. "Stop generating response") ensures that keyboard-only and screen reader users can abort requests instantly and elegantly.
**Action:** Always place an interrupt button adjacent to primary action buttons during streaming states and include standard transition properties to make the UI feel smooth and non-blocking.
