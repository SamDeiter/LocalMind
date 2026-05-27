# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-22 - [Accessible Sidebar Navigation & Safety]
**Learning:** To avoid invalid ARIA structures and improve keyboard accessibility, sidebar list items should use a flat interactive structure (avoiding nested buttons). Using `group-focus-within:opacity-100` ensures that secondary action buttons (like delete/export) are discoverable by keyboard users when the parent item or any of its children receive focus. Destructive actions like deletion must always be guarded by a confirmation dialog to prevent data loss.
**Action:** Replace nested interactive elements with semantic buttons and use CSS focus-within triggers for hidden actions. Always implement `confirm()` for destructive UI operations.
