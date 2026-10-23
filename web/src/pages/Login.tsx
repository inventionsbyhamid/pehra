import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useSession } from "../App";
import { Mark } from "../components/Mark";
import { setTimeZone } from "../format";
import { useTitle } from "../hooks";

export default function Login() {
  useTitle("Sign in");
  const { setUser } = useSession();
  const [user, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.login(user.trim(), password);
      setTimeZone(r.timezone);
      setUser(r.user);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <main className="login" id="main">
      <div className="login-copy">
        <div className="brand"><Mark size={36} /><span className="brand-name">Watchful</span></div>
        <h1 className="login-title">Your cameras, watched for the moments that matter.</h1>
        <p className="lede">Say what to look out for in plain words: someone falls, a car comes into the drive, a person at the back door at night. OpenCV watches every frame, an AI checks each flagged moment, and you can ask what happened.</p>
      </div>
      <form className="panel login-form stack" onSubmit={submit}>
        <h2 className="h2">Sign in</h2>
        <label className="field"><span>Name</span>
          <input className="input" autoComplete="username" value={user} onChange={(e) => setName(e.target.value)} required autoFocus />
        </label>
        <label className="field"><span>Password</span>
          <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p className="notice notice-error" role="alert">{error === "That name or password is wrong." ? "That name or password is wrong. Check the details in the testing instructions and try again." : error}</p>}
        <button className="btn btn-primary" disabled={busy || !user.trim() || !password}>{busy ? "Signing in" : "Sign in"}</button>
        <p className="xsmall muted">Demo login for judges. <Link to="/terms">Terms and privacy</Link></p>
      </form>
    </main>
  );
}
