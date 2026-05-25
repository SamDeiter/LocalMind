# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-24 - [Destructive Actions & Session Decoupling]
**Learning:** Users require explicit confirmation before irreversible actions like deleting conversations to prevent data loss. Furthermore, starting a 'New Chat' must explicitly reset internal session identifiers (like `currentConvId`) to ensure the subsequent message is correctly handled as a new thread, preventing accidental message linkage or leakage. Icon-only buttons should always use semantic labels and accessible focus states.
**Action:** Always wrap delete operations in confirmation dialogs and verify state resets during session transitions. Use ARIA labels and focus-visible triggers for interactive components.
