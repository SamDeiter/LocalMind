/**
 * Template Loader Utility
 * Dynamically loads HTML templates into mount points.
 */

export async function loadTemplate(id, url) {
    const mount = document.getElementById(id);
    if (!mount) {
        console.warn(`Mount point ${id} not found.`);
        return;
    }

    try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`Failed to fetch template: ${url}`);
        const html = await response.text();
        mount.innerHTML = html;
        
        // Handle pointer-events-none for containers like editor mount
        if (id === "app-editor-mount") {
            // The template itself should have pointer-events-auto if it's visible
            const editorEl = mount.querySelector("#editorPanel");
            if (editorEl) {
                // We keep the container pointer-events-none so it doesn't block clicks when hidden,
                // but when shown the toggle logic should handle it.
                // For now, let's just make sure the child is accessible.
                editorEl.style.pointerEvents = "auto";
            }
        }
        
    } catch (error) {
        console.error(`Error loading template ${url}:`, error);
    }
}

export async function loadAllTemplates() {
    console.log("Loading UI templates...");
    await Promise.all([
        loadTemplate("app-sidebar-mount", "templates/sidebar.html"),
        loadTemplate("app-editor-mount", "templates/editor_panel.html"),
        loadTemplate("app-main-mount", "templates/main_panel.html"),
        loadTemplate("app-right-mount", "templates/right_panel.html"),
        loadTemplate("app-modal-mount", "templates/settings_modal.html")
    ]);
    console.log("UI templates loaded.");
}
