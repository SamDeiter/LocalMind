# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-20 - [Robust DOM Selection for Nested Interactive Elements]
**Learning:** When implementing keyboard-accessible lists with nested action buttons, using `data-id` or similar unique identifiers is essential for reliably managing element states (like disabling buttons during async tasks) across re-renders or when multiple items share similar visual labels. Relying on `aria-label` for selection is fragile if the label content changes dynamically (e.g., adding "(selected)").
**Action:** Always include a unique `data-*` identifier on list items to facilitate robust DOM queries for nested elements.
