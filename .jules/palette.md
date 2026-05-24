# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2024-05-24 - [Keyboard Discoverability & Semantic Lists]
**Learning:** Elements that only appear on hover (like action buttons in a list) are invisible to keyboard users unless explicitly handled. Using `group-focus-within:opacity-100` ensures these actions become visible when the list item or any of its children receive focus. Additionally, avoid nesting interactive elements (like a button inside a clickable div) by using a flat structure with a main button for the primary action.
**Action:** Use `group-focus-within` for hover-actions and maintain flat, semantic HTML for list items to ensure WCAG compliance and predictable screen reader behavior.
