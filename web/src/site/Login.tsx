import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { TryDemo } from "./actions";
import brand from "./brand";
import { noSignIn, useProviders } from "./providers";
import SignInButtons from "./SignInButtons";
import SiteLayout from "./SiteLayout";

// what /auth/<provider>/callback and /auth/link send back in ?error=
const PROBLEMS: Record<string, string> = {
  signin: "Sign in did not finish. Please try again.",
  cancelled: "Sign in was cancelled. Try again when you are ready.",
  setup: "This way of signing in is not set up on this server yet.",
  link: "This sign in link has expired or was already used. Ask for a new one.",
  busy: "Too many sign in tries from this address. Wait a minute and try again.",
};

export default function Login() {
  const [code, setCode] = useState<string | null>(null);
  const providers = useProviders();
  const none = noSignIn(providers);
  const offered = Array.isArray(providers) && providers.length > 0;

  useEffect(() => {
    setCode(new URLSearchParams(window.location.search).get("error"));
  }, []);
  const problem = code ? (PROBLEMS[code] ?? PROBLEMS.signin) : null;

  return (
    <SiteLayout>
      <div className="wrap head-block narrow login-page">
        <h1>Sign in to {brand.name}</h1>
        {offered && <p className="lead">New here? Signing in creates your society on the Free plan.</p>}
        {problem && (
          <p className="login-problem" role="alert">
            {problem}
          </p>
        )}
        {code === "setup" && !none && (
          <div className="login-action">
            <p>You can try the demo meanwhile.</p>
            <TryDemo className="cta cta-line" />
          </div>
        )}
        <div className="login-action">
          <SignInButtons className="cta" />
        </div>
        <p className="login-small">
          By continuing you agree to the <Link to="/terms">terms</Link> and the <Link to="/privacy">privacy notice</Link>.
        </p>
      </div>
    </SiteLayout>
  );
}
