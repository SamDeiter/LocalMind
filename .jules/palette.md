# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-16 - [Standardized Conversation Controls & Safety]
**Learning:** Icon-only buttons in dense lists (like the conversation sidebar) require explicit ARIA labels to be accessible to screen readers. Furthermore, replacing generic emojis with standard Material Symbols improves visual consistency with the rest of the UI. For destructive actions like deletion, a confirmation dialog paired with a success toast provides the necessary safety and feedback loops for a polished UX.
**Action:** Standardize on Material Symbols for all interactive icons and always implement confirmation-plus-toast patterns for destructive operations.
