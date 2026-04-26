document.addEventListener("DOMContentLoaded", () => {
    document.body.classList.add("is-loaded");

    const toggle = document.querySelector("[data-nav-toggle]");
    const menu = document.querySelector("[data-nav-menu]");

    if (toggle && menu) {
        toggle.addEventListener("click", () => {
            const expanded = toggle.getAttribute("aria-expanded") === "true";
            toggle.setAttribute("aria-expanded", expanded ? "false" : "true");
            menu.classList.toggle("is-open", !expanded);
        });

        menu.querySelectorAll("a").forEach((link) => {
            link.addEventListener("click", () => {
                toggle.setAttribute("aria-expanded", "false");
                menu.classList.remove("is-open");
            });
        });

        window.addEventListener("resize", () => {
            if (window.innerWidth > 1060) {
                toggle.setAttribute("aria-expanded", "false");
                menu.classList.remove("is-open");
            }
        });
    }
});
