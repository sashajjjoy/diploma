document.addEventListener("DOMContentLoaded", () => {
    document.body.classList.add("is-loaded");

    const navbar = document.querySelector(".navbar");
    const navbarInner = document.querySelector(".navbar-inner");
    const toggle = document.querySelector("[data-nav-toggle]");
    const menu = document.querySelector("[data-nav-menu]");

    if (navbar && navbarInner && toggle && menu) {
        const COMPACT_WIDTH = 1280;

        const closeMenu = () => {
            toggle.setAttribute("aria-expanded", "false");
            menu.classList.remove("is-open");
        };

        const linksAreWrapped = () => {
            const links = Array.from(menu.querySelectorAll(".nav-link"));
            if (links.length < 2) {
                return false;
            }
            const firstTop = Math.round(links[0].getBoundingClientRect().top);
            return links.some((link) => Math.round(link.getBoundingClientRect().top) !== firstTop);
        };

        const syncNavbarLayout = () => {
            closeMenu();
            navbar.classList.remove("navbar-compact");

            const shouldCompact =
                window.innerWidth <= COMPACT_WIDTH ||
                navbarInner.scrollWidth > navbarInner.clientWidth + 4 ||
                linksAreWrapped();

            navbar.classList.toggle("navbar-compact", shouldCompact);
        };

        toggle.addEventListener("click", () => {
            if (!navbar.classList.contains("navbar-compact")) {
                return;
            }
            const expanded = toggle.getAttribute("aria-expanded") === "true";
            toggle.setAttribute("aria-expanded", expanded ? "false" : "true");
            menu.classList.toggle("is-open", !expanded);
        });

        menu.querySelectorAll("a").forEach((link) => {
            link.addEventListener("click", () => {
                closeMenu();
            });
        });

        window.addEventListener("resize", syncNavbarLayout);
        syncNavbarLayout();
    }
});
