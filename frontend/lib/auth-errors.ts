export type AuthErrorKind =
  | "credentials"
  | "network"
  | "server"
  | "unavailable"
  | "unknown"
  | "external"

export interface AuthErrorInfo {
  kind: AuthErrorKind
  title: string
  body: string
  fieldLevel: boolean
}

export function classifyAuthError(status: number | null): AuthErrorInfo {
  if (status === 400 || status === 401 || status === 422) {
    return {
      kind: "credentials",
      title: "Incorrect email or password",
      body: "Check your credentials and try again.",
      fieldLevel: true,
    }
  }
  if (status === null || status === 502 || status === 504) {
    return {
      kind: "network",
      title: "Cannot reach the unbork backend",
      body: "Check your reverse proxy or server status. If you're using Tailscale or a VPN, confirm you're connected.",
      fieldLevel: false,
    }
  }
  if (status === 503) {
    return {
      kind: "unavailable",
      title: "unbork is starting up",
      body: "The server or database is currently unavailable. Wait a moment and try again.",
      fieldLevel: false,
    }
  }
  if (status >= 500) {
    return {
      kind: "server",
      title: "Server error",
      body: "A server error occurred. Check your unbork logs for details.",
      fieldLevel: false,
    }
  }
  return {
    kind: "unknown",
    title: "Sign in failed",
    body: "Something went wrong. Try again in a moment.",
    fieldLevel: false,
  }
}

/** Why a directory sign-in (SSO, LDAP) was refused, keyed by the code the
 *  backend's `ExternalLoginRefused` carries. An SSO callback redirects to
 *  `/login?sso_error=<code>`; the login page shows this. */
const EXTERNAL_LOGIN_ERRORS: Record<string, string> = {
  no_access: "Your directory account is not in any group that has access. Ask an administrator.",
  not_provisioned: "You do not have an account here yet. Ask an administrator for one.",
  email_in_use:
    "An account with your email address already exists. Ask an administrator to link it to your directory account.",
  disabled: "Your account is disabled.",
  no_email: "Your identity provider did not send an email address for your account.",
  bad_email: "Your identity provider sent an email address that cannot be used.",
  directory_unavailable: "The directory could not be reached. Try again, or ask an administrator.",
  unknown_provider: "This sign-in method is no longer available. Reload the page.",
}

export function classifyExternalLoginError(code: string): AuthErrorInfo {
  return {
    kind: "external",
    title: "Directory sign-in refused",
    body: EXTERNAL_LOGIN_ERRORS[code] ?? "Signing in through your identity provider failed. Try again, or ask an administrator.",
    fieldLevel: false,
  }
}
