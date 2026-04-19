# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-20 - [Safety and Consistency in Conversational Interfaces]
**Learning:** Destructive actions like deleting a conversation must be guarded by confirmation dialogs to prevent accidental data loss. Furthermore, icon-only buttons require explicit ARIA labels for accessibility and should use a consistent icon set (e.g., Material Symbols) instead of platform-dependent emojis to ensure a professional and unified visual language across environments.
**Action:** Implement confirmation dialogs for all destructive UI actions and enforce the use of Material Symbols with ARIA labels for all icon-only interactive elements.
