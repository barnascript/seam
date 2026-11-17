// Where the API lives. Empty means "same place as this page" (the backend serves it).
// On GitHub Pages the page is static, so it talks to the hosted backend instead.
window.SEAM_API = location.hostname.endsWith("github.io") ? "__SEAM_API__" : "";
