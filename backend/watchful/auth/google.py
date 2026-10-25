"""Google sign in: the server side authorization code flow with PKCE (S256), state and nonce, through authlib.
The ID token is verified against Google's published keys: signature, issuer, audience, expiry, nonce and a verified
email. Importing this module also registers the fake provider, so AUTH_PROVIDERS=fake works in development."""
import logging
import time

from authlib.integrations.httpx_client import AsyncOAuth2Client
from fastapi import Request
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from watchful.auth.providers import AuthError, Flow, Identity  # importing it registers "fake"
from watchful.config import Settings
from watchful.registry import register

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ["https://accounts.google.com", "accounts.google.com"]
SCOPE = "openid email profile"
CERTS_TTL_S = 3600  # Google rotates its keys over days; a key we have not seen triggers a refetch anyway
CLOCK_LEEWAY_S = 60
HTTP_TIMEOUT_S = 10


@register("auth", "google")
class GoogleAuth:
    name = "google"
    label = "Google"

    def __init__(self, settings: Settings, transport=None, **_):
        self.client_id = settings.google_client_id
        self.client_secret = settings.google_client_secret
        self.configured = bool(self.client_id and self.client_secret)
        self.transport = transport  # tests put a mock here
        self._certs: tuple[float, KeySet] | None = None

    def _client(self, redirect_uri: str) -> AsyncOAuth2Client:
        if not (self.client_id and self.client_secret):
            raise AuthError("setup", "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not set")
        return AsyncOAuth2Client(self.client_id, self.client_secret, scope=SCOPE, redirect_uri=redirect_uri,
                                 code_challenge_method="S256", transport=self.transport, timeout=HTTP_TIMEOUT_S)

    async def start(self, request: Request, flow: Flow) -> str:
        async with self._client(flow.redirect_uri) as client:
            url, _ = client.create_authorization_url(AUTHORIZE_URL, state=flow.state, code_verifier=flow.code_verifier,
                                                     nonce=flow.nonce, prompt="select_account")
        return url

    async def finish(self, request: Request, flow: Flow) -> Identity:
        code = request.query_params.get("code")
        if not code:
            raise AuthError("signin", "the callback has no code")
        try:
            async with self._client(flow.redirect_uri) as client:
                token = await client.fetch_token(TOKEN_URL, code=code, code_verifier=flow.code_verifier)
                if not token.get("id_token"):
                    raise AuthError("signin", "Google sent no ID token")
                claims = await self._verify(client, token["id_token"], flow.nonce)
        except AuthError:
            raise
        except Exception as e:  # network, an error answer from Google, a malformed token
            raise AuthError("signin", f"the Google token exchange failed: {type(e).__name__}: {e}") from e
        return Identity("google", claims["sub"], claims["email"].strip().lower(), claims.get("name"))

    async def _keys(self, client, refresh: bool) -> KeySet:
        if refresh or self._certs is None or time.monotonic() - self._certs[0] > CERTS_TTL_S:
            res = await client.request("GET", CERTS_URL, withhold_token=True)  # without the user's access token
            res.raise_for_status()
            self._certs = (time.monotonic(), KeySet.import_key_set(res.json()))
        return self._certs[1]

    async def _verify(self, client, id_token: str, nonce: str) -> dict:
        for refresh in (False, True):
            try:
                token = jwt.decode(id_token, await self._keys(client, refresh), algorithms=["RS256"])
                break
            except (JoseError, ValueError) as e:
                if refresh:
                    raise AuthError("signin", f"the ID token signature does not check out: {e}") from e
        try:
            jwt.JWTClaimsRegistry(
                leeway=CLOCK_LEEWAY_S,
                iss={"essential": True, "values": ISSUERS},
                aud={"essential": True, "value": self.client_id},
                exp={"essential": True},
                sub={"essential": True},
                email={"essential": True},
                nonce={"essential": True, "value": nonce},
            ).validate(token.claims)
        except JoseError as e:
            raise AuthError("signin", f"the ID token claims do not check out: {e}") from e
        if token.claims.get("email_verified") is not True:
            raise AuthError("signin", "Google has not verified this email address")
        return token.claims
