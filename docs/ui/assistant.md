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

**1. Install it where the service can see it.** LabDog runs as the
`labdog` system user with `ProtectHome=true`, so anything under a home
directory is invisible to it — a per-user install will not be found. Use
the apt repository, which puts the binary on the system path:

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

This opens a browser, authenticates against your subscription, and prints
a token valid for one year. It is not saved anywhere — copy it.

**3. Paste it into the provider's Subscription token field.** It is stored
encrypted at rest exactly like an API key, never returned by the API, and
injected only into the CLI process. Leaving the field blank falls back to
whatever the host is already authenticated as.

Renew once a year by running `claude setup-token` again and editing the
provider.

> **Why LabDog strips `ANTHROPIC_API_KEY`.** The CLI resolves credentials
> in a fixed order, and `ANTHROPIC_API_KEY` outranks the subscription
> token. If that variable were present in LabDog's environment — quite
> likely, since the Anthropic provider wants one — the CLI would bill your
> API account instead, silently and with no difference in output. When a
> subscription token is configured, LabDog removes `ANTHROPIC_API_KEY` and
> `ANTHROPIC_AUTH_TOKEN` from the CLI's environment so you get the billing
> you asked for.

API keys are encrypted at rest and never returned by the API. Editing a
provider and leaving the key field blank keeps the stored key.

### Data egress

Each provider shows whether using it sends host data off your network.
A provider that does is blocked twice over: it needs its own **Allow this
provider to receive host data off my network** checkbox *and* the global
`ai.allow_cloud_providers` setting. Both default to off, so a fresh install
never sends anything anywhere until you decide it should.

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
