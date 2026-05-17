# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-20 - [Accessible Interactive Lists]
**Learning:** Making list items interactive requires more than just click handlers. To be truly accessible, items must have `tabindex="0"`, `role="button"`, and explicit keyboard event listeners for 'Enter' and 'Space'. Additionally, nested action buttons must use `group-focus-within` to remain visible when the parent or sibling is focused via keyboard, and should be disabled during asynchronous operations to prevent race conditions.
**Action:** Always implement full keyboard navigation patterns (tabindex, roles, key handlers) and state-based button disabling for any custom interactive list components.
