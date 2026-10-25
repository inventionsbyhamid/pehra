import { createContext, useContext, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api, whenSignedOut } from "./api";
import { setTimeZone } from "./format";
import { Shell } from "./components/Shell";
import Login from "./pages/Login";
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
      <Route path="*" element={<Workspace />} />
    </Routes>
  );
}

function Workspace() {
  const [user, setUser] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);
  const location = useLocation();

  useEffect(() => {
    whenSignedOut(() => setUser(null));
    api.me().then((m) => { setTimeZone(m.timezone); setUser(m.user); }).catch(() => setUser(null)).finally(() => setChecked(true));
  }, []);

  if (!checked) return null;

  if (location.pathname === "/terms" && !user) return <Terms standalone />;

  if (!user) return <SessionContext.Provider value={{ user, setUser }}><Login /></SessionContext.Provider>;

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
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Shell>
    </SessionContext.Provider>
  );
}
