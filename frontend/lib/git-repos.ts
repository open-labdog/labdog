export function detectAuthFromUrl(url: string): "ssh_key" | "https" | "unknown" {
  if (url.startsWith("git@") || url.startsWith("ssh://")) return "ssh_key"
  if (url.startsWith("https://")) return "https"
  return "unknown"
}
