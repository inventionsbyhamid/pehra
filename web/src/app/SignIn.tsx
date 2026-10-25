import { useTitle } from "../hooks";
import brand from "../site/brand";
import SignInButtons from "../site/SignInButtons";
import SiteLayout from "../site/SiteLayout";

const PROBLEMS: Record<string, string> = {
  signin: "Sign in did not finish. Please try again.",
  cancelled: "Sign in was cancelled. Try again when you are ready.",
  setup: "This way of signing in is not set up on this server yet.",
  link: "This sign in link has expired or was already used. Ask for a new one.",
  busy: "Too many sign in tries from this address. Wait a minute and try again.",
};

// Signed out on an app page (a Telegram link to an incident, say): the site's sign in, coming back here after.
export default function SignIn() {
  useTitle("Sign in");
  const code = new URLSearchParams(window.location.search).get("error");
  const problem = code ? PROBLEMS[code] ?? PROBLEMS.signin : null;
  const next = window.location.pathname + window.location.search.replace(/[?&]error=[^&]*/, "");
  return (
    <SiteLayout>
      <div className="wrap head-block narrow login-page">
        <h1>Sign in to {brand.name}</h1>
        <p className="lead">Sign in to open your society's control room. You come back to this page after.</p>
        {problem && <p className="login-problem" role="alert">{problem}</p>}
        <div className="login-action">
          <SignInButtons className="cta" next={next} />
        </div>
        <p className="login-small">A sign in link from your society's admin works too.</p>
      </div>
    </SiteLayout>
  );
}
