# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-20 - [Reusable Dynamic Textarea Sizing]
**Learning:** Hard-coded textarea heights often lead to awkward double-scrollbars or wasted whitespace. A generic `autoResize` utility that accepts both elements and events allows for a consistent "expanding input" feel across different modules (Chat, Task Creation, Pipeline Nodes). Re-binding this utility after dynamic DOM updates (like rendering pipeline steps) is essential to maintain the UX.
**Action:** Use the generic `autoResize` utility for all multi-line text inputs to ensure the interface adapts fluidly to user content.
