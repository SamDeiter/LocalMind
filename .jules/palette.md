# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-22 - [Data Safety & Visual Feedback in Conversation Management]
**Learning:** In a local-first application, users value explicit control and confirmation for data-destructive actions. Reverting from emojis to standard Material Symbols for actions like "delete" and "export" provides a more professional and predictable interface, while ARIA labels ensure these icon-only buttons remain accessible. Success toasts provide the necessary closure for operations like deletion.
**Action:** Implement confirmation dialogs for all destructive actions and use success toasts to confirm completion. Standardize on Material Symbols for common actions to ensure visual consistency and accessibility.
