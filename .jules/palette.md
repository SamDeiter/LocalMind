# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-22 - [Sidebar Accessibility & Feedback]
**Learning:** Avoid nesting interactive elements (e.g., buttons inside a `role="button"` div) as it creates invalid ARIA structures and confuses screen readers. Instead, use distinct sibling buttons for different actions. Providing immediate visual feedback (toasts) and confirmation for destructive actions significantly improves user confidence and safety.
**Action:** Use semantic button elements for all interactions and ensure destructive actions have confirmation steps and visible success/error feedback.
