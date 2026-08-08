# Assistant

**Path:** `/assistant`

The Assistant lets you hand an investigation to a connected language model.
You describe what you want checked; it works through LabDog's own tools,
and every command it runs is classified, bounded, and written to the audit
log.

It is off by default. Two things must be true before it will do anything:
a provider is configured on the [AI Providers](#ai-providers) page, and
`ai.enabled` is set to `1` under [Settings](settings.md).

---

## Starting a session

1. Pick an **autonomy level** (see below).
2. Tick the **hosts in scope**. The assistant can only touch hosts on this
   list — it cannot reach anything else, even if you mention it by name in
   the prompt.
3. Describe the task and press **Start session** (or Ctrl+Enter).

Output streams in as it is produced. Each command the assistant runs
appears as a badge showing the command, its safety classification, and a
one-line result, so you can follow the reasoning without re-reading the
raw log output it read.

When the session finishes you can ask follow-up questions in the same
conversation; it keeps the full context of what it already found.

Good prompts are specific about the question, not the method:

- *"Check whether any service failed to start after the last reboot on node-1."*
- *"Compare installed package versions on web-1 and web-2 and tell me what differs."*
- *"Disk usage on backup-1 jumped yesterday. Find out what grew."*

---

## Running checks on a schedule

The assistant also runs unattended. Two actions appear in the normal
action list, so you schedule them from [Schedules](scheduled-actions.md)
exactly like any other action — same cron, same run history, same
per-host queueing.

| Action | Scope |
|--------|-------|
| **AI check (per host)** | One session per target host, that host alone in scope. Right for "check each of these". |
| **AI check (whole group)** | One session with every group member in scope at once. Right for "compare these" or "which one is the odd one out" — per-host sessions cannot see each other, so they cannot answer that. |

Both take the same parameters:

- **What to investigate** — the mission, in plain language.
- **Autonomy** — as above; leave it at read-only unless you would be
  comfortable with the check acting unattended at 3am.
- **AI provider** — `0` uses the default. A recurring check is a good
  place to pick a local model, since it runs every night whether or not
  anything is wrong.
- **Permitted tools** — comma-separated, or blank for all of them.

That last one is the main cost control. Naming a subset bounds what the
check can spend *and* what it can reach: a log sweep limited to
`query_loki` cannot open an SSH session at all. `list_hosts` and
`get_host_facts` are always available, since without an inventory the
assistant cannot find the host ids the other tools need.

The report appears in the run detail like any other action's output.

### Which tool is cheaper

Every tool result is re-read on each later turn, so a large one is paid
for repeatedly rather than once. Reading logs through Loki and reading
them over SSH are not simply cheaper or dearer than one another — it
depends on the question:

- **Counting or comparing** — much cheaper through Loki. A
  `count_over_time` query has Loki do the work and returns a few numbers,
  where the SSH equivalent ships thousands of log lines into the context
  so the model can count them itself.
- **The same question across several hosts** — cheaper through Loki, as
  one query rather than one session per host.
- **Reading specific error text** — about even. `journalctl -n 50` is
  bounded by construction.

You do not have to take that on trust. Every tool call records how much
text it returned, so after a week of real runs you can see which tools
are actually consuming your budget.

---

## Autonomy levels

| Level | What it may do |
|-------|----------------|
| **Read-only** (default) | Only commands that report state. Anything that would change a host is refused and reported back. |
| **Approval required** | Reads run immediately; changes wait for your approval. The approval workflow ships in a later release — until then this behaves like read-only. |
| **Full auto** | May change hosts on its own. |

A command's classification comes from parsing the command, not from what
the model says it does. Anything LabDog does not recognise as read-only is
treated as a change, so an unfamiliar command is never run unsupervised at
the read-only level.

**A denylist applies at every level, including full auto.** Commands that
destroy data or take a host off the network — `rm -rf /`, `mkfs`, writing
directly to a block device, piping a download into a shell, flushing the
whole firewall ruleset — are blocked outright. There is no setting that
permits them.

---

## What the assistant can see

- **Host inventory and cached OS facts** — hostnames, addresses, OS and
  kernel versions, for hosts in scope.
- **Live host state over SSH** — using the same key LabDog already uses for
  that host.
- **Metrics** — instant PromQL against the default Mimir instance
  registered under [Grafana](../ui/README.md), if one is configured.

Command output is scanned for credentials before it enters the transcript,
so passwords, tokens, and private keys in a config file are replaced with a
placeholder rather than being sent to the model. This is a safety net, not
a guarantee: the stronger protection is that read-only is the default and
the host allowlist bounds what can be read at all.

---

## Limits

Every session is bounded. It ends when the assistant is finished, or when
it reaches whichever of these comes first:

| Setting | Default | Meaning |
|---------|---------|---------|
| `ai.max_iterations` | 15 | Model turns |
| `ai.max_commands` | 20 | Shell commands across all hosts |
| `ai.max_tokens_total` | 200000 | Prompt + completion tokens |
| `ai.wall_clock_seconds` | 900 | Wall-clock run time |

A session stopped by a limit still produces a report: the assistant spends
one final turn summarising what it established and what remains unverified,
so the work is not wasted.

---

<a id="ai-providers"></a>

## AI Providers

**Path:** `/ai-providers`

Three kinds of provider are supported:

| Type | Use for |
|------|---------|
| **OpenAI-compatible** | Ollama, vLLM, LM Studio, OpenRouter, OpenAI — anything speaking the chat-completions API. Needs a base URL such as `http://localhost:11434/v1`. |
| **Anthropic** | The Anthropic Messages API. Leave the base URL blank for the public API. |
| **Claude CLI** | The `claude` binary installed on the LabDog host, billed to your Claude subscription instead of API credits. Single-shot: it can write reports and verify verdicts but cannot run tools, so it cannot drive an investigation. |

### Using the Claude CLI with a subscription

The CLI backend bills your Claude Pro/Max/Team subscription rather than
metered API usage, which is worth having for something that runs nightly.
Setting it up takes three steps.

**1. Install it where the service can see it.** *Container deploys can
skip this step — the official image bundles the binary.* On a package
install, LabDog runs as the `labdog` system user with `ProtectHome=true`,
so anything under a home directory is invisible to it and a per-user
install will not be found. Use the apt repository, which puts the binary
on the system path:

```bash
sudo install -d -m 0755 /etc/apt/keyrings
sudo curl -fsSL https://downloads.claude.ai/keys/claude-code.asc \
  -o /etc/apt/keyrings/claude-code.asc
gpg --show-keys /etc/apt/keyrings/claude-code.asc   # 31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE
echo "deb [signed-by=/etc/apt/keyrings/claude-code.asc] \
https://downloads.claude.ai/claude-code/apt/stable stable main" \
  | sudo tee /etc/apt/sources.list.d/claude-code.list
sudo apt update && sudo apt install claude-code
```

Updates then arrive through `apt upgrade` like any other package — which
is exactly what LabDog's own package module manages, so it can keep its
AI backend patched alongside everything else.

**2. Mint a token on your own machine, not the server.** The server has no
browser and the service user cannot log in:

```bash
claude setup-token
```

The exchange puts **three** different strings in front of you, and only
the last one belongs in LabDog. They are easy to mix up, so in order:

| # | What you see | What it is | Where it goes |
|---|---|---|---|
| 1 | `https://claude.com/cai/oauth/authorize?…` | The authorization **URL** | Open it in a browser |
| 2 | `bfBNeAU0…#DQYiwPwb…` | The authorization **code** | Back into the waiting terminal |
| 3 | `sk-ant-oat01-…` | The **token** | The provider form |

So: `setup-token` prints the URL and waits. Open it, approve the request,
and the callback page shows you a code — paste that back at the terminal
prompt, exactly as shown, including the `#…` part (that segment is the
`state` value, and the CLI checks it matches). The command then prints the
token, which is the only one of the three LabDog ever sees.

Two things that catch people out. **It must be the same terminal session**
— the URL carries a `code_challenge` whose matching verifier exists only
in the process that printed it, so a reopened terminal means starting
over. And **the URL must be one unbroken line**; if your terminal wrapped
it, the copied challenge will not match.

**3. Paste the token into the provider's Subscription token field.** It
starts `sk-ant-oat01-` — LabDog rejects anything that does not, because
the alternative is storing a credential that is guaranteed to fail and
only saying so at the first session. It is stored encrypted at rest
exactly like an API key, never returned by the API, and injected only
into the CLI process. Leaving the field blank falls back to whatever the
host is already authenticated as.

Then press **Test**. It runs the binary *and* makes one small
authenticated call, so a green result means the token genuinely works —
not merely that `claude` is installed.

Renew once a year by running `claude setup-token` again and editing the
provider. Doing so also invalidates the previous token, which is the
fastest way to retire one you think has leaked.

#### Running LabDog in a container

The official image bundles the `claude` binary, so there is nothing to
install. Paste your token into the provider form and it works.

The image is correspondingly larger — the CLI is about 123 MB on disk,
against roughly 130 MB compressed for the rest of LabDog, so expect the
pull to be a little over half again as big as it used to be. That is the
cost of the CLI backend being available without an image of your own.

The binary is installed from Anthropic's apt repository during the build,
so its signature is verified by the repository key, and it is refreshed on
every image build rather than pinned — a stale copy of a network-facing
binary is a liability with no upside.

Two things follow from LabDog owning the process:

- **The token never lives in the image.** It is stored encrypted in the
  database and injected into the CLI's environment at spawn time, so
  rebuilding or replacing the image does not disturb your credentials.
- **The CLI's state is isolated to `/var/lib/labdog/claude-cli`,** not
  `$HOME`. Mount a volume at `/var/lib/labdog` — you already need one for
  action packs — and the CLI's session state persists with everything
  else. See the credential-precedence note below for why the location
  matters.

If you would rather not carry the binary at all, use an Anthropic
provider: no binary, and it can run tools, so it can drive assistant
sessions and scheduled checks the CLI backend cannot. The only thing you
give up is subscription billing.

> **Why LabDog controls the CLI's credentials and config directory.** The
> CLI resolves credentials in a fixed order, and the subscription token
> sits low in it. Two things outrank it, both of which fail the same way:
> the wrong account is used, silently, with no error and no difference in
> the output. The only symptom is the invoice.
>
> `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` outrank it from the
> environment — quite likely to be present, since the Anthropic provider
> wants one. LabDog removes both from the CLI's environment whenever a
> subscription token is configured.
>
> A stored login also outranks it from disk. If anyone has ever run
> `claude login` as the LabDog service user, the credentials file it wrote
> wins over the token you pasted into the form. LabDog therefore points the
> CLI at `/var/lib/labdog/claude-cli` via `CLAUDE_CONFIG_DIR`, a directory
> it owns, so no interactive session can leave a login where it would
> shadow your configuration.
>
> Both apply only when a subscription token is configured. Leave the field
> blank and nothing is touched — that case is an operator who authenticated
> the CLI on the host deliberately, and isolating it would break the only
> credential such a setup has.

API keys are encrypted at rest and never returned by the API. Editing a
provider and leaving the key field blank keeps the stored key.

### Data egress

Each provider shows whether using it sends host data off your network. A
provider that does stays blocked until `ai.allow_cloud_providers` is
enabled in Settings. It is off by default, so a fresh install never sends
anything anywhere until you decide it should.

One switch covers the whole instance, deliberately. A provider added
months from now cannot start sending data off-network on its own — it
still waits on a policy decision you made once, on purpose.

Note that the Claude CLI counts as off-network. The binary runs locally,
but it is an authenticated client talking to Anthropic, so the data still
leaves; it is gated exactly like a hosted API.

### Pricing and budgets

Enter the provider's rates per million tokens by hand — an
OpenAI-compatible endpoint has no way to report its own pricing. Leave both
at `0` for a self-hosted model, which makes the money budgets a no-op for it
while the token and iteration caps still apply.

Set `ai.currency` under [Settings](settings.md) to the currency you actually
pay in — euro, pound, krona, and the other common ones are all available.
**It is a label, not a conversion.** LabDog stores whatever number you type
and never applies an exchange rate, so switching the setting relabels your
existing figures rather than recalculating them. Enter every provider's rates
in the currency you picked.

Each provider type offers suggested models as one-click presets. Anthropic
presets fill in their published rates; local Ollama presets fill in `0`,
which is the true cost of a model you host yourself. A preset for a paid
hosted model deliberately leaves the rate fields alone rather than guessing —
a stale figure that looks authoritative is worse than a blank one.

Every field with a small **i** beside it explains itself when clicked,
including what separates input from output cost and how the per-provider cap
relates to the global budgets.

The **Usage and budget** panel shows spend today and this month against
your limits, plus a per-day breakdown.

| Setting | Meaning |
|---------|---------|
| `ai.budget_daily_usd` | Maximum spend per day (`0` = unlimited) |
| `ai.budget_monthly_usd` | Maximum spend per calendar month (`0` = unlimited) |
| `ai.budget_warn_pct` | Warn once this percentage of a budget is spent |
| Per-provider **Monthly cap** | A ceiling for one provider, useful when a free local model and a paid one are both configured |

Budgets are enforced, not advisory. Once a limit is reached, new sessions
are refused, and a session already running stops at its next step rather
than finishing on your card.
