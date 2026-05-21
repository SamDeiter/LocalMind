# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-20 - [Accessible Conversation Sidebar & Action Feedback]
**Learning:** Using non-semantic elements (like a `div`) with click listeners for primary navigation items hinders keyboard accessibility and screen reader support. Separating the item into a main `<button>` for the action (loading a chat) and secondary action buttons (export/delete) ensures valid ARIA structures. Additionally, using `group-focus-within:opacity-100` ensures that hidden-on-hover actions are also visible when navigating via keyboard, while confirmation dialogs and success toasts provide essential feedback for destructive or long-running operations.
**Action:** Use semantic `<button>` elements for all interactive sidebar items and ensure secondary actions are keyboard-discoverable using focus-within triggers.
