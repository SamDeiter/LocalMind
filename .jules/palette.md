# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-20 - [Accessible List Item Structure]
**Learning:** Nested interactive elements (e.g., a clickable `div` containing buttons) create invalid ARIA structures and confuse screen readers. Using a semantic `<button>` for the main item title alongside separate action buttons, rather than making the entire container a button, ensures a clear, accessible hierarchy.
**Action:** Always separate main list item actions from secondary actions using distinct semantic buttons instead of nesting roles.
