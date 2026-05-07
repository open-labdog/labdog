# Trivy Ignore Rationale

Per-CVE notes for entries in [`.trivyignore`](pathname:///../.trivyignore) at
the repo root. The CI `trivy-scan` job (`.github/workflows/ci.yml`,
around line 362) runs the Aqua `trivy-action` against the test
image and gates on HIGH/CRITICAL findings; `.trivyignore` is picked
up automatically from the repo root.

The job already passes `ignore-unfixed: "true"`, so Trivy only
reports CVEs that *do* have a fix available. Anything in this file
is therefore a CVE with no upstream fix, which would otherwise
break the build until the affected base image or dependency
publishes one.

Process for adding a new entry:

1. Confirm there's no upstream fix (`apt-cache policy <pkg>` on the
   image base; check the distribution security tracker).
2. Add the CVE id to `.trivyignore` with a comment describing the
   package and the nature of the issue.
3. Add a row to the table below: when added, why it can't be fixed
   yet, and what would unblock removing the ignore.
4. Re-evaluate every release (or quarterly, whichever is sooner).
   If a fix has shipped, drop the ignore and let CI confirm the
   image is clean.

---

## Current ignores

| CVE | Package | Added | Why ignored | Re-evaluate when |
|---|---|---|---|---|
| `CVE-2025-69720` | `ncurses` | 2026-04-21 | Buffer overflow; affected, no fix released by upstream Debian. ncurses ships in the `python:3.12-slim` base image. | Debian publishes a patched `libncurses6` / `libtinfo6`. Upgrade base image, drop ignore, confirm scan clean. |
| `CVE-2026-29111` | `systemd` | 2026-04-21 | Arbitrary code execution via spurious IPC; affected, no fix released. systemd libs (`libsystemd0`, `libudev1`) ship in `python:3.12-slim` even though we don't run a systemd init inside the container. | Debian publishes a patched `libsystemd0`. Drop ignore on next image rebuild that picks up the fix. |
| `CVE-2026-35385` | `openssh-client` | 2026-04-21 | Required by Ansible for SSH transport to managed hosts. No fix released. Removing `openssh-client` is not an option — it's a hard dependency for every sync. | OpenSSH project publishes a fix and Debian backports it. |
| `CVE-2026-35386` | `openssh-client` | 2026-05-04 | Same package as above; surfaced when the Trivy DB picked up a new advisory. Same constraint and same removal trigger. | OpenSSH project publishes a fix and Debian backports it. |
| `CVE-2026-35414` | `openssh-client` | 2026-04-21 | Same package as above; tracked separately because Trivy reports them as distinct advisories. Same constraint and same removal trigger. | OpenSSH project publishes a fix and Debian backports it. |
| `CVE-2026-25210` | `libexpat1` | 2026-04-24 | Integer-overflow info disclosure; affected, no fix released as of the add date. expat is pulled in transitively (Python stdlib XML, ansible-runner). | Debian ships a patched `libexpat1`. |
| `CVE-2026-27135` | `libnghttp2-14` | 2026-04-24 | Post-session-termination DoS; affected, no fix released as of the add date. nghttp2 is pulled in by `curl` / HTTP/2 client libs in the runtime image. | Debian ships a patched `libnghttp2-14`. |
| `CVE-2026-4878`  | `libcap2` | 2026-05-04 | Capability-handling flaw; affected, no fix released. `libcap2` is pulled in by the base image runtime (used by systemd / process tooling); not removable. | Debian ships a patched `libcap2`. |
| `CVE-2026-33845` | `libgnutls30t64` | 2026-05-04 | TLS-side issue; affected, no fix released. `libgnutls30t64` is a transitive base-image dependency. | Debian ships a patched `libgnutls30t64`. |
| `CVE-2026-33846` | `libgnutls30t64` | 2026-05-06 | DoS via heap buffer overflow in DTLS handshake; affected, no fix released. Same package + transitive base-image dependency as `CVE-2026-33845`. | Debian ships a patched `libgnutls30t64`. |
| `CVE-2026-3833`  | `libgnutls30t64` | 2026-05-07 | Policy bypass via case-sensitive nameConstraints comparison; affected, no fix released. Same transitive base-image dependency as the two entries above. | Debian ships a patched `libgnutls30t64`. |

Last full review: **2026-05-07** (add date of the most recent
entries). Next review: **2026-08-07**, or whenever a new release is
cut, whichever is sooner.

---

## How to re-evaluate

For each entry above:

```bash
# 1. Pull the current base image and check the package version.
docker run --rm python:3.12-slim apt-cache policy <package>

# 2. Look up the CVE on the Debian security tracker.
#    https://security-tracker.debian.org/tracker/<CVE>
#    A "fixed" status with a version <= the apt-cache policy version
#    means the ignore can be removed.

# 3. Locally rebuild the image and confirm the scan is clean.
docker build -t labdog:trivy-test .
trivy image --severity HIGH,CRITICAL --ignore-unfixed labdog:trivy-test
```

If a fix has shipped, delete the line from `.trivyignore`, delete
the row from the table above, and let CI confirm the image is
clean on the next run.
