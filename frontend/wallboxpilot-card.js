/* WallboxPilot entry point. Preserve the version query for the compatible existing implementation. */
const implementation = new URL('./abl-wallbox-card.js', import.meta.url);
implementation.search = new URL(import.meta.url).search;
await import(implementation.href);
