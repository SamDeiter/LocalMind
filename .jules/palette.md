# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-20 - [Accessible Drop Zones & Dynamic Textarea Growth]
**Learning:** Custom interactive elements like file drop zones are often invisible to screen readers and keyboard users if implemented as simple divs. Adding `role="button"`, `tabindex="0"`, and explicit keyboard listeners (Enter/Space) ensures parity with native controls. Additionally, textareas in task-heavy interfaces benefit significantly from dynamic height adjustment (`autoResize`), reducing friction during long-form input.
**Action:** Use a generalized `autoResize` utility for all multi-line inputs and always jail custom interactions with standard ARIA roles and keyboard support.
