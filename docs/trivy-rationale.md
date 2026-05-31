# Trivy Ignore Rationale

Per-CVE notes for entries in [`.trivyignore`](pathname:///../.trivyignore) at
the repo root. The CI `trivy-scan` job in
[`.github/workflows/ci.yml`](pathname:///../.github/workflows/ci.yml)
runs the Aqua `trivy-action` against the test image and gates on
HIGH/CRITICAL findings; `.trivyignore` is picked up automatically
from the repo root.

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
| `CVE-2026-25210` | `libexpat1` | 2026-04-24 | Integer-overflow info disclosure; affected, no fix released. expat is pulled in transitively (Python stdlib XML, ansible-runner). | Debian ships a patched `libexpat1`. |

Last full review: **2026-05-27** — dropped 9 entries (systemd, 3×
openssh-client, libnghttp2-14, libcap2, 3× libgnutls30t64) after
Debian shipped fixes in trixie; the blanket `apt-get upgrade -y` in
the Dockerfile now pulls them. Next review: **2026-08-27**, or
whenever a new release is cut, whichever is sooner.

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
