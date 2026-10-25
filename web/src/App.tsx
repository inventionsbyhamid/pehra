import { createContext, lazy, Suspense, useContext, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, whenSignedOut } from "./api";
import { setTimeZone } from "./format";
import { Shell } from "./components/Shell";
import Login from "./pages/Login";
import Welcome, { Join } from "./pages/Welcome";
import Cameras from "./pages/Cameras";
import CameraView from "./pages/CameraView";
import Alerts from "./pages/Alerts";
import Ask from "./pages/Ask";
import TimelinePage from "./pages/Timeline";
import Find from "./pages/Find";
import People from "./pages/People";
import Numbers from "./pages/Numbers";
import Terms from "./pages/Terms";
import Landing from "./site/Landing";
import Pricing from "./site/Pricing";
import SiteTerms from "./site/Terms";
import Privacy from "./site/Privacy";
import Refunds from "./site/Refunds";
import SiteLogin from "./site/Login";

const CamPage = lazy(() => import("./cam/CamPage"));

type Session = { user: string | null; setUser: (u: string | null) => void };
const SessionContext = createContext<Session>({ user: null, setUser: () => {} });
export const useSession = () => useContext(SessionContext);

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
      <Route path="*" element={<Workspace />} />
    </Routes>
  );
}

function Workspace() {
  const [user, setUser] = useState<string | null>(null);
  const [society, setSociety] = useState<boolean>(false);
  const [checked, setChecked] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  function load() {
    api.me()
      .then((m) => {
        setTimeZone(m.timezone);
        setUser(m.user.name || m.user.email || "you");
        setSociety(m.workspace !== null);
      })
      .catch(() => setUser(null))
      .finally(() => setChecked(true));
  }

  useEffect(() => {
    whenSignedOut(() => setUser(null));
    load();
  }, []);

  if (!checked) return null;

  if (location.pathname === "/terms" && !user) return <Terms standalone />;

  if (!user) return <SessionContext.Provider value={{ user, setUser }}><Login /></SessionContext.Provider>;

  if (location.pathname.startsWith("/join/")) {
    const token = location.pathname.slice("/join/".length);
    return <SessionContext.Provider value={{ user, setUser }}><Join token={token} onDone={() => { navigate("/app", { replace: true }); load(); }} /></SessionContext.Provider>;
  }

  if (!society) return <SessionContext.Provider value={{ user, setUser }}><Welcome onDone={load} /></SessionContext.Provider>;

  return (
    <SessionContext.Provider value={{ user, setUser }}>
      <Shell>
        <Routes>
          <Route path="/" element={<Cameras />} />
          <Route path="/app" element={<Cameras />} />
          <Route path="/cameras/:id" element={<CameraView />} />
          <Route path="/cameras/:id/timeline" element={<TimelinePage />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/alerts/:id" element={<Alerts />} />
          <Route path="/ask" element={<Ask />} />
          <Route path="/find" element={<Find />} />
          <Route path="/people" element={<People />} />
          <Route path="/numbers" element={<Numbers />} />
          <Route path="/terms" element={<Terms />} />
          <Route path="*" element={<Navigate to="/app" replace />} />
        </Routes>
      </Shell>
    </SessionContext.Provider>
  );
}
