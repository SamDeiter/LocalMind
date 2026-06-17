# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-16 - [Accessibility & Feedback in Conversation Management]
**Learning:** Destructive actions like deleting a conversation require a confirmation step to prevent accidental data loss. Providing immediate feedback via toast notifications after background actions (like export or delete) improves user confidence and perceived system responsiveness. Replacing standard emojis with consistent icon library symbols (like Material Symbols) and adding ARIA labels ensures a more professional and accessible experience for all users.
**Action:** Implement confirmation dialogs for destructive actions, provide post-action feedback via toasts, and use semantic icon-only buttons with explicit ARIA labels.
