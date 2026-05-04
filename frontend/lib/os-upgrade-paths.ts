/**
 * Static mapping of Linux distribution codenames to their next major-release
 * codename, used to pre-populate the "Target codename" field in the
 * linux-os-upgrade action dialog.
 *
 * Keyed by lowercase codename. Codenames are unique across the major distros
 * in practice, so distro ID is not part of the key.
 *
 * TODO: there is no authoritative source for "what comes next". When a new
 * major release ships, add an entry here by hand. A future enhancement could
 * derive this at build time from e.g. the Debian `distro-info` package or
 * Ubuntu's /usr/share/distro-info/ubuntu.csv, but that adds infrastructure
 * complexity for a map that churns every 1-2 years.
 */
export const OS_UPGRADE_PATHS: Record<string, string> = {
  // Debian
  bullseye: "bookworm",   // 11 -> 12
  bookworm: "trixie",     // 12 -> 13
  trixie: "forky",        // 13 -> 14 (forky is named; release TBD)

  // Ubuntu (LTS to LTS only)
  focal: "jammy",         // 20.04 -> 22.04
  jammy: "noble",         // 22.04 -> 24.04
  noble: "oracular",      // 24.04 -> 24.10 (interim; next LTS 26.04 not yet named)
}

/**
 * Look up the next codename for a given current codename. Returns undefined
 * if the codename is unknown (new distro, typo, or end-of-path).
 */
export function nextCodename(current: string | null | undefined): string | undefined {
  if (!current) return undefined
  return OS_UPGRADE_PATHS[current.toLowerCase()]
}
