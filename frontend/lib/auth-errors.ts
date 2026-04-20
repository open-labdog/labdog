export type AuthErrorKind =
  | "credentials"
  | "network"
  | "server"
  | "unavailable"
  | "unknown"

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
      title: "Cannot reach Barricade backend",
      body: "Check your reverse proxy or server status. If you're using Tailscale or a VPN, confirm you're connected.",
      fieldLevel: false,
    }
  }
  if (status === 503) {
    return {
      kind: "unavailable",
      title: "Barricade is starting up",
      body: "The server or database is currently unavailable. Wait a moment and try again.",
      fieldLevel: false,
    }
  }
  if (status >= 500) {
    return {
      kind: "server",
      title: "Server error",
      body: "A server error occurred. Check your Barricade logs for details.",
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
