import { StrictMode } from "react";
import { createRoot, hydrateRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";
import "./pages.css";

const root = document.getElementById("root")!;
const app = (
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>
);

// scripts/prerender.mjs marks the page it rendered; any other route served that HTML renders fresh
const here = location.pathname.replace(/\/+$/, "") || "/";
if (root.dataset.prerendered === here) hydrateRoot(root, app);
else createRoot(root).render(app);
