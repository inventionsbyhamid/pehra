import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, whenSignedOut } from "../api";
import { setTimeZone } from "../format";
import Welcome, { Join } from "../pages/Welcome";
import type { Me, Purposes } from "../types";
import AddCamera from "./AddCamera";
import CameraPage from "./CameraPage";
import ControlRoom from "./ControlRoom";
import History from "./History";
import IncidentPage from "./IncidentPage";
import Notice from "./Notice";
import People from "./People";
import { SessionContext } from "./session";
import Settings from "./Settings";
import { Shell } from "./Shell";
import SignIn from "./SignIn";
import WatchEditor from "./WatchEditor";
import "./ui/app.css";

// Outside and older addresses (the Telegram link /incidents/{id}, the first version's pages) land on their page here.
function Moved({ to }: { to: (id: string) => string }) {
  const { id = "" } = useParams();
  const { search } = useLocation();
  return <Navigate to={`${to(id)}${search}`} replace />;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/app" element={<ControlRoom />} />
      <Route path="/app/cameras/new" element={<AddCamera />} />
      <Route path="/app/cameras/:id" element={<CameraPage />} />
      <Route path="/app/cameras/:id/history" element={<History />} />
      <Route path="/app/watches/new" element={<WatchEditor />} />
      <Route path="/app/incidents/:id" element={<IncidentPage />} />
      <Route path="/app/people" element={<People />} />
      <Route path="/app/settings" element={<Navigate to="/app/settings/society" replace />} />
      <Route path="/app/settings/privacy/notice" element={<Notice />} />
      <Route path="/app/settings/:tab" element={<Settings />} />
      <Route path="/incidents/:id" element={<Moved to={(id) => `/app/incidents/${id}`} />} />
      <Route path="/cameras/:id" element={<Moved to={(id) => `/app/cameras/${id}`} />} />
      <Route path="/cameras/:id/timeline" element={<Moved to={(id) => `/app/cameras/${id}/history`} />} />
      <Route path="/people" element={<Navigate to="/app/people" replace />} />
      <Route path="/numbers" element={<Navigate to="/app/settings/numbers" replace />} />
      <Route path="*" element={<Navigate to="/app" replace />} />
    </Routes>
  );
}

export default function Workspace() {
  const [me, setMe] = useState<Me | null>(null);
  const [checked, setChecked] = useState(false);
  const [purposes, setPurposes] = useState<Purposes | null>(null);
  const location = useLocation();
  const navigate = useNavigate();

  const load = useCallback(() => {
    api.me()
      .then((m) => {
        setTimeZone(m.timezone);
        setMe(m);
      })
      .catch(() => setMe(null))
      .finally(() => setChecked(true));
  }, []);

  useEffect(() => {
    whenSignedOut(() => setMe(null));
    load();
  }, [load]);

  const society = me?.workspace?.id;
  useEffect(() => {
    if (society) api.purposes().then(setPurposes).catch(() => setPurposes({}));
  }, [society]);

  const signOut = useCallback(() => {
    api.logout().catch(() => {}).finally(() => {
      setMe(null);
      navigate("/app", { replace: true });
    });
  }, [navigate]);

  if (!checked) return null;
  if (!me) return <SignIn />;

  if (location.pathname.startsWith("/join/")) {
    const token = location.pathname.slice("/join/".length);
    return <Join token={token} onDone={() => { navigate("/app", { replace: true }); load(); }} />;
  }

  if (!me.workspace) return <Welcome onDone={load} />;

  return (
    <SessionContext.Provider value={{ me, purposes, reload: load, signOut }}>
      <Shell>
        <AppRoutes />
      </Shell>
    </SessionContext.Provider>
  );
}
