# Off-machine copies

`flow-restore` brings a dozen agents back after a reboot because their state is
on disk. That is also its limit: the disk is one machine. `flow-sync` pushes the
same state into an encrypted [restic](https://restic.net) repository over ssh,
and pulls it back onto a machine that has nothing.

Reboot is the cheap case. This is for the expensive ones — a reinstall, a second
laptop, a stolen one.

## The premise, measured

`claude --resume <uuid>` needs the transcript and nothing else. A transcript
copied into a directory it never belonged to resumes with its full history:

```console
$ cp .../46582906-….jsonl ~/.claude/projects/-home-you-probe-proj/
$ cd ~/probe/proj && claude -p --resume 46582906-… "what did I first ask you?"
Про ексапостиларий, глас и как зафиксировать в памяти их связь для будущего.
```

Two things follow, and the whole design rests on them:

- The directory name must be the slug of the working directory you resume
  **from** (`flow_sessions.project_dir`). Get that wrong and `--resume` finds
  nothing.
- The `cwd` recorded *inside* the entries is history. It does not have to match,
  so a restore never rewrites transcript contents — only directory names.

Resuming appends to the same file. Transcripts only grow, which is what makes
both deduplication cheap and the conflict rule below decidable.

## What travels

| Path | Why | Restored by |
|---|---|---|
| `~/.claude/projects/` | the context itself | file copy, dirs renamed |
| `~/.tmux/claude-sessions.tsv` | which session ran in which window | merge |
| `~/.claude.json` | per-project trust, allowed tools, MCP servers | merge, `projects` only |
| `~/.tmux/claude-map/` | pane → session UUID | file copy, names renamed |
| `~/.tmux/tab-state/` | timestamps behind the tab colour | file copy |
| `~/.tmux/resurrect/` | the tmux layout itself | file copy |

`~/.claude/.credentials.json` is excluded by default. The transcripts are the
context; the credentials are the account, and a re-login costs a minute.

Not covered, on purpose: git working trees (that is what a remote is for) and
running processes (that is variant A — a tmux living on the server, which
buys nothing here since the server reboots too).

## Setting up

```bash
mkdir -p ~/.config/tmux-core-flow
install -m600 /dev/null ~/.config/tmux-core-flow/sync-password
head -c32 /dev/urandom | base64 > ~/.config/tmux-core-flow/sync-password

cp config/sync.example.yaml ~/.config/tmux-core-flow/sync.yaml
$EDITOR ~/.config/tmux-core-flow/sync.yaml    # set repo:

flow-sync init
flow-sync push
flow-sync status
```

**Keep a copy of the password file somewhere else.** It is the only key; restic
cannot recover a repository without it, and that is the property you are paying
for. Losing the laptop and the password together loses the backups too.

The repo needs no software on the server — restic's `sftp:` backend speaks to
the ssh daemon that is already there.

Then keep it fed:

```cron
*/15 * * * * ~/.local/bin/flow-sync push --if-stale 900 --quiet
17 4 * * 0   ~/.local/bin/flow-sync forget >/dev/null
```

`--if-stale` makes the schedule idempotent, so a second driver can call the same
command without doubling the work.

### Pushing from the hook instead

`claude-hook-notify` knows the exact moment a transcript stops changing — the
`Stop` event. Adding one line to the end of it pushes then:

```bash
flow-sync push --detach --if-stale 300 || true
```

`--detach` returns immediately and the work happens in a background process, so
the network cannot hold up a pane. It is not wired in by default because the
hook's one guarantee is that it can never block or fail an agent, and a hook
that opens a socket is a weaker version of that promise. The cron entry gives
you the same thing with none of the risk; use the hook when a 15-minute window
of possible loss is too wide.

## Bringing it back

On the machine it came from — after a reinstall, or once the disk was replaced:

```console
$ flow-sync pull
restoring snapshot latest into ~/.cache/tmux-core-flow/staging …

restored: 166 file(s), 0 already current
index: +57 session(s)

next: flow-session-index --update && flow-restore
```

Then the usual `flow-restore --arm --all`.

On a *different* machine, where `$HOME` is not what it was, the paths encoded
into every directory name are wrong. `pull` reads the snapshot's own home from
its paths, compares, and renames as it restores:

```console
$ flow-sync pull
remapping paths: /home/you -> /Users/you
```

Pass `--remap /old/path=/new/path` when more than the home moved (a project tree
that used to live in `~/work` and now lives in `/opt`), or `--no-remap` to
restore verbatim. The index `cwd` column is rewritten by the same mapping, so
`flow-restore` `cd`s somewhere that exists.

## What a pull will not do

It never destroys. For each incoming file:

| Situation | What happens |
|---|---|
| nothing here | copied in |
| identical | left alone |
| snapshot is newer **and** at least as long | copied in — a transcript only grows, so it is a superset |
| local is newer **and** at least as long | left alone |
| one newer, the other longer | parked as `<file>.from-sync` |

The last row is a session that was resumed on two machines. Merging two
divergent transcripts is not something a backup tool gets to guess at, so both
are kept and you pick.

One thing the index does *not* bring back: rows the snapshot has but whose
`seen` is over 30 days old. That is `flow-session-index`'s own retention, applied
to the incoming reading for the same reason it is applied locally — a window
nobody has touched in a month is not what you are restoring. The transcripts
themselves are unaffected and `claude --resume <uuid>` still works by hand.

The index and `~/.claude.json` are never copied over, because both describe this
machine as well as the snapshot's. The index goes through the same
`flow_sessions.merge` that a cron refresh uses — rows only the snapshot has are
added, and this machine wins on a window both know about. From the config, only
`projects` entries that are missing here are imported; startup counters, machine
id and onboarding state stay local.

`--force` overrides all of it, and `--dry-run` prints the same summary while
touching nothing.

Pull before starting agents on that machine. A running Claude Code holds
`~/.claude.json` in memory and writes it back on exit, so a merge landing
underneath one is a merge that gets overwritten.

## Commands

| | |
|---|---|
| `flow-sync init` | create the repository (once) |
| `flow-sync push` | snapshot the current state |
| `flow-sync push --if-stale S` | ...only if the last one is older than S seconds |
| `flow-sync push --detach` | ...in the background, return now |
| `flow-sync pull` | bring the newest snapshot back |
| `flow-sync pull --snapshot ID` | ...a specific one |
| `flow-sync status` | repo, last push, what would go, latest snapshot |
| `flow-sync snapshots` | what is in the repository |
| `flow-sync forget` | apply retention, reclaim space |

Any restic backend works — `sftp:host:/path`, a local disk, S3. The transport is
restic's; what to send and how it lands is `lib/flow_sync.py`, so swapping the
transport is six argv builders.
