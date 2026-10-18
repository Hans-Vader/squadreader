import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { useViewerStore } from "./state/viewerStore";
import "./style.css";
import "./theme-apple.css";

// Deployment-specific theme: only the squadreader.com build is made with
// VITE_THEME=apple (npm run build:apple), and there this bundle is the replay
// viewer at /replay. Setting the attribute synchronously before render
// activates the token overrides with no flash. The agent build never sets it,
// so altaisquad keeps the default indigo theme.
if (import.meta.env.VITE_THEME === "apple") {
  document.documentElement.setAttribute("data-theme", "apple");
}

const root = document.getElementById("root");
if (!root) throw new Error("missing #root element");
createRoot(root).render(<StrictMode><App /></StrictMode>);

// Dev-only handle for the end-to-end harness: progressive loading is mostly
// INVISIBLE state (buffered-to, stalled, loading) and asserting on pixels
// alone would not tell a stall from a stutter.
if (import.meta.env.DEV) {
  (window as unknown as Record<string, unknown>).__viewer = useViewerStore;
}
