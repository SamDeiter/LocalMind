# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-04-15 - [Enhanced Conversation Management UX & Accessibility]
**Learning:** Icon-only buttons (like Export and Delete) require explicit `aria-label` attributes for screen reader accessibility. Replacing generic emojis with themed Material Symbols (e.g., 'download', 'delete') improves visual consistency and professionalism. Providing a deletion confirmation prevents data loss, and toast notifications offer necessary feedback for asynchronous operations.
**Action:** Use Material Symbols and `aria-label` for all icon-only buttons; implement confirmation for destructive actions and toast feedback for all CRUD operations.
