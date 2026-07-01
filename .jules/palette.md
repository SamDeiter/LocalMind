# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-16 - [Focus Management for Dynamic Chat Controls]
**Learning:** When dynamic controls like a "Stop" button appear in response to a user action (e.g., sending a message), they must receive focus to remain accessible to keyboard and screen reader users. Simply showing the button is insufficient if the user's focus remains on a now-disabled or irrelevant element. Returning focus to the primary input upon completion ensures a seamless continuation of the task.
**Action:** Always implement explicit focus management when toggling visibility of primary interactive elements in the chat flow.
