# epiplan

A planner that runs in your terminal and stays in sync with **intra.epitech.eu**. It shows your
registered activities and project deadlines, plans how much time to spend on each project (broken
into concrete tasks), reminds you to register for important activities, prepares you for reviews,
keynotes and defenses, and can pop a desktop notification 10 minutes before anything starts.

Everything is editable, and it works in **English or French**.

## Install

**Linux / macOS:**

```
bash install.sh
```

**Windows** (in PowerShell, from this folder):

```
powershell -ExecutionPolicy Bypass -File install.ps1
```

The installer sets everything up on your computer, asks you to pick a language and (optionally) a
daily break to keep free, and prints where this guide lives afterwards. Then open a **new terminal**
so the `epiplan` command is on your `PATH`. On Linux/macOS, if it warns that `~/.local/bin` isn't on
your `PATH`, add this to your `~/.bashrc` (or `~/.zshrc`):

```
export PATH="$HOME/.local/bin:$PATH"
```

## First run

```
epiplan login      # opens Chrome once; log in with your Epitech Microsoft account
epiplan            # open the planner
```

Tick **"Stay signed in"** during that first login so epiplan can refresh its access on its own later.
No graphical browser (e.g. over SSH)? Use `epiplan login --paste` and paste the `user` cookie from
intra.epitech.eu instead. Your password is never stored — only the intra's own session.

## Keys in the planner

| Key | Action |
|-----|--------|
| `j`/`k`, arrows | move up / down |
| `n`/`p`, `t` | next / previous week, back to today |
| `Enter` | details |
| `a` | add a personal entry |
| `e` | edit title / time / room |
| `o` | edit notes in your `$EDITOR` |
| `space` | mark done (also ticks a work block) |
| `d` | delete (yours) / hide (intra) / dismiss (a registration suggestion) |
| `H` | show hidden entries |
| `u` | undo your edits on an intra entry |
| `w` | edit a project's tasks and hours (opens your `$EDITOR`) |
| `T` | start a task list for a project |
| `g` | sync the project's GitHub repo and suggest tasks from the code |
| `l` | log hours worked today |
| `W` | overview of all projects |
| `r` | activities you could register for |
| `O` | open the selected item on the intra (to register) |
| `c` | ask the intra assistant a question |
| `s` | sync now |
| `?` | help |
| `q` | quit |

## Commands

| Command | What it does |
|---------|--------------|
| `epiplan` | open the planner |
| `epiplan sync` | pull activities from the intra |
| `epiplan list [DAYS]` | print the agenda + work plan (default 7 days) |
| `epiplan add "Title" "when" [end]` | add a personal entry, e.g. `epiplan add "Gym" "tomorrow 18:00" 19:30` |
| `epiplan ask "question"` | ask the intra assistant one question |
| `epiplan chat` | chat with the intra assistant |
| `epiplan repos` | link + sync each project's GitHub repo and suggest tasks |
| `epiplan repo "name" PATH\|owner/repo` | link a project to its repo (path or GitHub slug) |
| `epiplan notifications on\|off\|test` | background reminders (Linux) |
| `epiplan lang en\|fr` | change the language |
| `epiplan config` | edit all settings in your `$EDITOR` |

## The work plan

epiplan spreads the hours left on each project across the days before its deadline, earliest
deadline first, in your free time. Press `w` on a project (or one of its work blocks) to edit the
task list — one `HOURS  what you'll do` per line — so each block names exactly what to do. It also
adds preparation time before each review, keynote and defense you're registered for.

## The assistant

Press `c` in the planner, or run `epiplan chat` (or `epiplan ask "…"`), to ask about your intra in
plain language:

- "what do I have today / tomorrow / this week?"
- "when is my next defense / review / keynote / exam?"
- "what should I work on now?" · "am I behind?"
- "how many hours are left on Corewar?"
- "what do I need to register for?"
- "when is the Tardis deadline?"
- "how many credits do I have?" · "what's my GPA?" · "show my grades" · "what's my netsoul?"

Each sync also pulls your intra **profile** (credits, GPA, grades, netsoul/log time, and the rest),
so the assistant can answer those too. It answers **only** from your own synced intra data — it runs entirely offline, needs no account or
API key, and sends nothing anywhere. By design it only talks about your intra and **won't write code**
or answer off-topic questions.

## GitHub repositories &amp; task suggestions

epiplan can link each project to its GitHub repository, sync with it, and suggest tasks from the code:

- **`epiplan repos`** links every project (auto-detecting a local clone whose folder name matches, or
  a slug you set), runs `git fetch` to reflect GitHub, and prints each repo's status plus suggested
  tasks. In the planner, press **`g`** on a project to do this for one and open its task list seeded
  with the suggestions.
- **Linking:** if auto-detection misses one, link it manually with
  `epiplan repo "Corewar" ~/path/to/clone` or `epiplan repo "Corewar" EpitechPGE1-2025/G-CPE-200-...`.
  A slug is cloned into `~/.local/share/epiplan/repos/` and pulled on later syncs.
- **What it suggests:** open `TODO`/`FIXME` comments in your code become tasks, and it flags common
  missing deliverables (README, Makefile for C projects, tests). It also shows commits, last commit
  date, and how many commits you're behind/ahead of GitHub (visible in a project's details).
- **Ticking off done tasks:** every repo sync ticks the suggested tasks the code shows are finished —
  a `TODO` that's gone, or a README/Makefile/tests that now exist. Ticked tasks drop out of the
  schedule and count as worked. In the task editor (`w`) they show as `x 1  ...`; put an `x` in
  front of any task to tick it yourself. Auto-ticking never unticks a task.
- **Automatic:** once a project is linked, its repo also refreshes as part of the regular sync — at
  most every 30 minutes (`repo_sync_minutes`), so it stays in step with your schedule without you
  running anything. Set `sync_repos` to `false` to keep it fully on-demand. `g` and `epiplan repos`
  always refresh immediately.
- **Access:** for private Epitech repos it uses your existing clone, or `gh`/SSH auth to clone a slug
  (run `gh auth login` once, or have your SSH key set up). Git runs non-interactively, so it never hangs.

## Settings (`epiplan config`)

- `work_hours`, `max_work_hours_per_day`, `max_block_hours` — when and how much you work.
- `breaks` — recurring busy times, e.g. lunch. A break with a `label` shows in your week; without
  one it's just kept free. `if_activity` makes a break apply only on days containing that word.
- `notify_minutes`, `notify_sound` — the reminders.
- `important`, `prep_hours`, `prep_lead_days` — which activities matter and how long to prep.
- `register_activities` — which activities need a booked slot (matched by word in the title/type):
  defenses, follow-ups, reviews, keynotes, bootstraps, kick-offs. Add your own words here.
- `register_reminders_hours` — hours before such an activity to remind you to book, e.g. `[72, 48, 25]`.
- `deadline_reminders_hours` — hours before a project deadline to remind you to push/submit, e.g. `[24, 2]`.
- `repo_search_dirs` — folders to search for a project's local git clone when linking repos.
- `sync_repos`, `repo_sync_minutes` — refresh linked GitHub repos during sync, at most every N minutes (default 30).
- `lang` — `en`, `fr`, or empty to auto-detect.

## Notifications

`epiplan notifications on` installs a background job that checks every minute — even when the planner
is closed — and reminds you about:

- **anything starting in 10 minutes** (activities, your entries, planned work blocks);
- **booking slot activities** — defenses, follow-ups, reviews, keynotes, bootstraps and kick-offs need
  you to reserve a time slot. While the slot is open and you haven't registered, you get a nudge when
  it first opens, then again at 72h, 48h and 25h before it (the 25h one lands just before the usual
  24h booking window), so you don't forget to pick a slot;
- **project deadlines** — a push/submit reminder 24h and 2h before each deadline;
- **important activities** you still haven't registered for (a daily nudge).

It uses **systemd** on Linux and **Task Scheduler** on Windows. On macOS the planner and notifications
while it's open work, but the every-minute background check isn't set up automatically yet.

## How it runs on Windows

It's the same program as on Linux/macOS — Windows just wires it up a little differently:

- **Install:** `install.ps1` copies the app to `%LOCALAPPDATA%\epiplan-app\`, creates a virtual
  environment there, and installs `windows-curses` (for the terminal UI) plus Playwright (for login).
- **The `epiplan` command:** the installer writes a small `epiplan.cmd` launcher into
  `%LOCALAPPDATA%\epiplan-bin\` and adds that folder to your user `PATH`. Typing `epiplan` runs that
  `.cmd`, which just calls the venv's `python.exe` on `epiplan.py`. (Open a **new** terminal after
  installing so the new PATH is picked up.)
- **Settings and data** go under your user profile: `C:\Users\<you>\.config\epiplan\` and
  `C:\Users\<you>\.local\share\epiplan\`.
- **The UI** uses `windows-curses`, so it draws in Windows Terminal, PowerShell or cmd.
- **Login** opens your installed Google Chrome; `epiplan login --paste` works if you'd rather paste
  the cookie.
- **Notifications** are shown as Windows toast balloons (via PowerShell).
- **Background reminders** (`epiplan notifications on`) register a **Task Scheduler** job that runs
  `epiplan notify` every minute with `pythonw.exe` (so no console window flashes), even when the
  planner is closed. `epiplan notifications off` removes it.

Requires Python 3 (tick "Add to PATH" when installing it) and, for one-click login, Google Chrome.

## Where your data lives

- Settings: `~/.config/epiplan/config.json` (Windows: `C:\Users\<you>\.config\epiplan\config.json`)
- Planner + intra session: `~/.local/share/epiplan/` (Windows: `C:\Users\<you>\.local\share\epiplan\`)

Only your own account can read them. Nothing is sent anywhere except to intra.epitech.eu.

## Requirements

Python 3, and (for one-click login) Google Chrome. Works on Linux, macOS and Windows. The installer
handles the extras (`windows-curses` on Windows, Playwright for login). Without Chrome/Playwright,
`epiplan login --paste` works everywhere.
