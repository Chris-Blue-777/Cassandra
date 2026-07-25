(function () {
    const STORAGE_KEY = "cassandra.theme";
    const LEGACY_STORAGE_KEY = "theme";

    function storedTheme() {
        try {
            return (
                localStorage.getItem(STORAGE_KEY)
                || localStorage.getItem(LEGACY_STORAGE_KEY)
                || "light"
            );
        } catch (error) {
            return "light";
        }
    }

    function persistTheme(theme) {
        try {
            localStorage.setItem(STORAGE_KEY, theme);
            localStorage.setItem(LEGACY_STORAGE_KEY, theme);
        } catch (error) {
            // Theme still applies for the current page if storage is blocked.
        }
    }

    function isDarkTheme(theme) {
        return theme === "dark" || theme === "night";
    }

    function updateButtons(theme) {
        const isDark = isDarkTheme(theme);
        document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
            const icon = button.querySelector(".theme-toggle-icon");
            if (icon) {
                icon.textContent = isDark ? "☀" : "☾";
            }
            button.setAttribute(
                "aria-label",
                isDark ? "Switch to light mode" : "Switch to night mode"
            );
            button.setAttribute("aria-pressed", isDark ? "true" : "false");
            button.setAttribute("title", isDark ? "Light mode" : "Night mode");
        });
    }

    function applyTheme(theme, options) {
        const normalizedTheme = isDarkTheme(theme) ? "dark" : "light";
        const isDark = normalizedTheme === "dark";
        document.documentElement.classList.toggle("dark-mode", isDark);
        if (document.body) {
            document.body.classList.toggle("dark-mode", isDark);
        }
        updateButtons(normalizedTheme);
        if (!options || options.persist !== false) {
            persistTheme(normalizedTheme);
        }
    }

    function initTheme() {
        applyTheme(storedTheme(), { persist: false });
        document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
            button.addEventListener("click", () => {
                const nextTheme = document.documentElement.classList.contains("dark-mode")
                    ? "light"
                    : "dark";
                applyTheme(nextTheme);
            });
        });
    }

    window.CassandraTheme = {
        applyTheme,
        storedTheme,
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initTheme);
    } else {
        initTheme();
    }

    window.addEventListener("storage", (event) => {
        if (event.key === STORAGE_KEY || event.key === LEGACY_STORAGE_KEY) {
            applyTheme(storedTheme(), { persist: false });
        }
    });
}());
