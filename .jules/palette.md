# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-20 - [Accessible & Responsive Task Creation]
**Learning:** Complex form widgets like the task creation pipeline require careful attention to keyboard navigation and dynamic layout. Standardizing on a generic `autoResize` utility allows for consistent, responsive textareas across different modules. Improving accessibility via ARIA labels and roles on custom-styled interactive elements (like the file drop zone) significantly enhances the experience for power users and those using assistive technologies.
**Action:** Use a unified `autoResize` utility for all dynamic textareas and ensure every interactive element has an explicit accessible role and label.
