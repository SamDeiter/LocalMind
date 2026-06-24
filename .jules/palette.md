# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-24 - [Stateful UI Focus Management]
**Learning:** Adding a stateful "Stop" button requires more than just visibility toggling; it needs active focus management to ensure keyboard accessibility. Shifting focus to the stop button when it appears allows for immediate interruption of long AI responses, and returning it to the input field maintains the conversational flow.
**Action:** Always implement focus traps or focus shifts when toggling critical action buttons (Send/Stop/Cancel) to keep the interface accessible to non-mouse users.
