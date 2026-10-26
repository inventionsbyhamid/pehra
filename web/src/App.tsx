import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import Landing from "./site/Landing";
import Pricing from "./site/Pricing";
import SiteTerms from "./site/Terms";
import Privacy from "./site/Privacy";
import Refunds from "./site/Refunds";
import SiteLogin from "./site/Login";
import { AwsDocs, DocsHome, GettingStarted, HowItWorks, PrivacySafety, SelfHosting } from "./site/DocsPages";

// The public pages load at once (they are prerendered); the signed in app loads when opened. The device page
// (/cam/{token}) is its own build, cam.html, served by the backend.
const Workspace = lazy(() => import("./app/Workspace"));
const DemoStart = lazy(() => import("./app/DemoBar"));

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/pricing" element={<Pricing />} />
      <Route path="/terms" element={<SiteTerms />} />
      <Route path="/privacy" element={<Privacy />} />
      <Route path="/refunds" element={<Refunds />} />
      <Route path="/login" element={<SiteLogin />} />
      <Route path="/docs" element={<DocsHome />} />
      <Route path="/docs/getting-started" element={<GettingStarted />} />
      <Route path="/docs/how-it-works" element={<HowItWorks />} />
      <Route path="/docs/privacy-and-safety" element={<PrivacySafety />} />
      <Route path="/docs/self-hosting" element={<SelfHosting />} />
      <Route path="/docs/aws" element={<AwsDocs />} />
      <Route path="/demo" element={<Suspense fallback={null}><DemoStart /></Suspense>} />
      <Route path="*" element={<Suspense fallback={null}><Workspace /></Suspense>} />
    </Routes>
  );
}
