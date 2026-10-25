// The device page's own entry (cam.html): no router and none of the app's code, because this page alone is served
// with a policy that lets OpenCV.js build functions at run time (backend/watchful/api/device.py).
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import CamPage from "./CamPage";
import "../styles.css";

const token = decodeURIComponent(location.pathname.split("/")[2] ?? "");
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <CamPage token={token} />
  </StrictMode>,
);
