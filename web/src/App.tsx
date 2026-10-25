import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import Landing from "./site/Landing";
import Pricing from "./site/Pricing";
import SiteTerms from "./site/Terms";
import Privacy from "./site/Privacy";
import Refunds from "./site/Refunds";
import SiteLogin from "./site/Login";

// The public pages load at once (they are prerendered); the signed in app and the device page load when opened.
const Workspace = lazy(() => import("./app/Workspace"));
const CamPage = lazy(() => import("./cam/CamPage"));

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/pricing" element={<Pricing />} />
      <Route path="/terms" element={<SiteTerms />} />
      <Route path="/privacy" element={<Privacy />} />
      <Route path="/refunds" element={<Refunds />} />
      <Route path="/login" element={<SiteLogin />} />
      {/* a device link: no sign in, the token in the address is the camera's credential */}
      <Route path="/cam/:token" element={<Suspense fallback={null}><CamPage /></Suspense>} />
      <Route path="*" element={<Suspense fallback={null}><Workspace /></Suspense>} />
    </Routes>
  );
}
