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
import FindPerson from "./pages/FindPerson";
import People from "./pages/People";
import Numbers from "./pages/Numbers";
import Terms from "./pages/Terms";

type Session = { user: string | null; setUser: (u: string | null) => void };
const SessionContext = createContext<Session>({ user: null, setUser: () => {} });
export const useSession = () => useContext(SessionContext);

export default function App() {
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
          <Route path="/cameras/:id" element={<CameraView />} />
          <Route path="/cameras/:id/timeline" element={<TimelinePage />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/alerts/:id" element={<Alerts />} />
          <Route path="/ask" element={<Ask />} />
          <Route path="/find" element={<FindPerson />} />
          <Route path="/people" element={<People />} />
          <Route path="/numbers" element={<Numbers />} />
          <Route path="/terms" element={<Terms />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Shell>
    </SessionContext.Provider>
  );
}
