import { api, setToken } from "./api";

// WebAuthn exchanges ArrayBuffers; JSON needs base64url strings.
function b64urlToBuffer(value: string): ArrayBuffer {
  const pad = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + pad).replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

function bufferToB64url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// Convert the server's PublicKeyCredentialCreationOptions JSON (base64url
// challenge/user.id/excludeCredentials.id) into the ArrayBuffer shapes the
// browser API requires.
function decodeCreationOptions(o: any): PublicKeyCredentialCreationOptions {
  return {
    ...o,
    challenge: b64urlToBuffer(o.challenge),
    user: { ...o.user, id: b64urlToBuffer(o.user.id) },
    excludeCredentials: (o.excludeCredentials ?? []).map((c: any) => ({
      ...c,
      id: b64urlToBuffer(c.id),
    })),
  };
}

function decodeRequestOptions(o: any): PublicKeyCredentialRequestOptions {
  return {
    ...o,
    challenge: b64urlToBuffer(o.challenge),
    allowCredentials: (o.allowCredentials ?? []).map((c: any) => ({
      ...c,
      id: b64urlToBuffer(c.id),
    })),
  };
}

function encodeAttestation(cred: PublicKeyCredential): any {
  const r = cred.response as AuthenticatorAttestationResponse;
  return {
    id: cred.id,
    rawId: bufferToB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufferToB64url(r.clientDataJSON),
      attestationObject: bufferToB64url(r.attestationObject),
      transports: (r.getTransports?.() ?? []) as string[],
    },
  };
}

function encodeAssertion(cred: PublicKeyCredential): any {
  const r = cred.response as AuthenticatorAssertionResponse;
  return {
    id: cred.id,
    rawId: bufferToB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufferToB64url(r.clientDataJSON),
      authenticatorData: bufferToB64url(r.authenticatorData),
      signature: bufferToB64url(r.signature),
      userHandle: r.userHandle ? bufferToB64url(r.userHandle) : null,
    },
  };
}

/** Register a new passkey for the logged-in user. */
export async function registerPasskey(name: string): Promise<void> {
  const { nonce, options } = await api<{ nonce: string; options: any }>(
    "/api/auth/webauthn/register/options",
    { method: "POST" },
  );
  const cred = (await navigator.credentials.create({
    publicKey: decodeCreationOptions(options.publicKey ?? options),
  })) as PublicKeyCredential;
  await api("/api/auth/webauthn/register/verify", {
    method: "POST",
    body: JSON.stringify({ nonce, credential: encodeAttestation(cred), name }),
  });
}

/** Log in with a passkey. email optional → one-click (discoverable). Sets the
 * token on success; caller then fetches /api/auth/me. */
export async function loginWithPasskey(email?: string): Promise<void> {
  const { nonce, options } = await api<{ nonce: string; options: any }>(
    "/api/auth/webauthn/login/options",
    { method: "POST", body: JSON.stringify({ email: email || null }) },
  );
  const cred = (await navigator.credentials.get({
    publicKey: decodeRequestOptions(options.publicKey ?? options),
  })) as PublicKeyCredential;
  const { access_token } = await api<{ access_token: string }>(
    "/api/auth/webauthn/login/verify",
    { method: "POST", body: JSON.stringify({ nonce, credential: encodeAssertion(cred) }) },
  );
  setToken(access_token);
}
