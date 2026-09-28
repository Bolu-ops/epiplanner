#!/usr/bin/env python3
"""epiplan - a terminal planner synced with intra.epitech.eu.

Usage:
  epiplan                    open the planner (TUI)
  epiplan login              log in once in a Chrome window (--paste: paste the cookie instead)
  epiplan sync               pull activities from the intra
  epiplan list [DAYS]        print the agenda, work plan and registration reminders (default 7 days)
  epiplan add TITLE WHEN [END]   add a personal entry, e.g. epiplan add "Gym" "tomorrow 18:00" 19:30
  epiplan notifications on|off|test   background reminders (10 min before activities, registrations)
  epiplan config             edit settings (work hours, reminder delay, important keywords...)
  epiplan notify             one reminder check; the background timer runs this every minute
"""

import base64
import concurrent.futures
import contextlib
import getpass
import json
import locale
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"

# curses is Unix stdlib; on Windows it comes from the 'windows-curses' package. It is only needed
# for the planner UI, so a missing curses still lets the command-line parts run.
try:
    import curses
except ImportError:
    curses = None

# fcntl (Unix) / msvcrt (Windows) give the file lock that stops the planner and the reminder job
# from overwriting each other. If neither is available we simply run without the lock.
try:
    import fcntl
except ImportError:
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None

INTRA = "https://intra.epitech.eu"
CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "epiplan")
DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "epiplan")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
DATA_FILE = os.path.join(DATA_DIR, "planner.json")
STATE_FILE = os.path.join(DATA_DIR, "notify-state.json")
LOCK_FILE = os.path.join(DATA_DIR, ".lock")
SYSTEMD_DIR = os.path.expanduser("~/.config/systemd/user")

SYNC_DAYS_BEFORE = 7
SYNC_DAYS_AFTER = 60
BACKGROUND_SYNC_MINUTES = 5

DEFAULT_CONFIG = {
    "token": "",
    "lang": "",  # "" = auto-detect from the system, or "en" / "fr"
    "notify_minutes": 10,
    "notify_sound": True,
    "notify_work_blocks": True,
    "daily_reminder_hour": 9,
    "work_hours": "10:00-22:00",
    "max_work_hours_per_day": 6,
    "max_block_hours": 2,
    "important": ["kick-off", "kickoff", "kick off", "follow-up", "follow up", "followup", "review",
                  "defense", "defence", "soutenance", "keynote", "bootstrap", "exam", "delivery",
                  "rattrapage", "stumper", "hub"],
    "prep_hours": {"defense": 1.5, "keynote": 2, "review": 1, "follow-up": 0.5},
    "prep_lead_days": 2,
    # Slot-based activities need you to book a time slot. Any activity whose title or type contains
    # one of these words gets the registration reminders below (while the slot is open and you have
    # not registered yet).
    "register_activities": ["defense", "defence", "soutenance", "follow-up", "follow up", "followup",
                            "review", "keynote", "bootstrap", "kick-off", "kickoff", "kick off"],
    # Hours-before-the-activity at which to remind you to register. Epitech usually opens booking
    # ~24h before, so 25h nudges you right before it opens and the earlier ones give a heads-up.
    "register_reminders_hours": [72, 48, 25],
    # Hours-before a project deadline at which to remind you to submit / push.
    "deadline_reminders_hours": [24, 2],
    # Where to look for a project's local git clone when linking it to its GitHub repo.
    "repo_search_dirs": ["~", "~/Downloads", "~/Documents", "~/Projects", "~/repos", "~/delivery"],
    # Refresh linked GitHub repos as part of the regular sync, at most this often (minutes).
    "sync_repos": True,
    "repo_sync_minutes": 30,
    "breaks": [
        {"days": "mon-fri", "start": "13:00", "end": "14:30", "label": "Lunch"},
        {"days": "sat", "start": "00:00", "end": "16:00", "if_activity": "stumper"},
    ],
}

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_days(spec):
    """A day spec like 'mon-fri', 'sat', or 'mon,wed,fri' into a set of weekday numbers (Mon=0)."""
    days = set()
    for part in spec.lower().replace(" ", "").split(","):
        if "-" in part:
            start, end = (WEEKDAYS.index(p[:3]) for p in part.split("-"))
            days |= set(range(start, end + 1)) if start <= end else set(range(start, 7)) | set(range(end + 1))
        elif part:
            days.add(WEEKDAYS.index(part[:3]))
    return days


# ---------------------------------------------------------------- language

LANG = "en"

FR = {
    # errors / login
    "automatic login needs Playwright (pip install playwright), or use: epiplan login --paste":
        "la connexion automatique nécessite Playwright (pip install playwright), ou utilise : epiplan login --paste",
    "could not log in automatically - run: epiplan login":
        "connexion automatique impossible - lance : epiplan login",
    "intra refused the token": "l'intra a refusé le jeton",
    "intra answered HTTP {code}": "l'intra a répondu HTTP {code}",
    "cannot reach intra: {reason}": "impossible de joindre l'intra : {reason}",
    "intra did not return JSON (not logged in)": "l'intra n'a pas renvoyé de JSON (non connecté)",
    "intra: {message}": "intra : {message}",
    # generated display text
    "Deadline: {title}": "Rendu : {title}",
    "Join project {title}": "Projet {title}",
    "Work on {name}": "Travailler sur {name}",
    "Prep for {name}": "Préparer {name}",
    "Prep: {name}": "Prépa : {name}",
    "Register? {title}": "S'inscrire ? {title}",
    "Lunch": "Déjeuner",
    # agenda / describe
    "all day": "journée",
    "  - {left} left": "  - {left} restantes",
    " (guess, w to set)": " (estimation, w pour régler)",
    "  NOT ENOUGH TIME: {behind} short": "  TEMPS INSUFFISANT : {behind} de trop",
    " (full)": " (complet)",
    " (removed on intra)": " (retiré de l'intra)",
    " (hidden)": " (masqué)",
    "! not enough free time for {title}: {behind} short before {deadline}":
        "! pas assez de temps libre pour {title} : {behind} de trop avant {deadline}",
    "{count} to register for - see 'r' in epiplan":
        "{count} à laquelle t'inscrire - voir 'r' dans epiplan",
    # date pieces
    " (today)": " (aujourd'hui)",
    "epiplan  -  week of {a} to {b}": "epiplan  -  semaine du {a} au {b}",
    "   - {h} of project work planned": "   - {h} de travail projet prévues",
    # notifications
    "epiplan needs you to log in": "epiplan a besoin que tu te connectes",
    "Run: epiplan login": "Lance : epiplan login",
    "In {n} min: {title}": "Dans {n} min : {title}",
    "Register for {count}": "Inscris-toi à {count}",
    "Still not registered: {count}": "Toujours pas inscrit : {count}",
    "epiplan reminders are on": "les rappels epiplan sont activés",
    "You'll be told {n} minutes before your activities.":
        "tu seras prévenu {n} minutes avant tes activités.",
    "background reminders are off": "les rappels en arrière-plan sont désactivés",
    "background reminders are on (checked every minute, even when epiplan is closed)":
        "rappels en arrière-plan activés (vérifiés chaque minute, même epiplan fermé)",
    "In 10 min: Test activity": "Dans 10 min : activité de test",
    "09:00-12:00 @ Amphi-1  (this is a test)": "09:00-12:00 @ Amphi-1  (ceci est un test)",
    # column labels (padded to 8)
    "intra   ": "intra   ", "mine    ": "perso   ", "work    ": "travail ",
    "register": "inscris ", "break   ": "pause   ",
    # help
    "Navigation   j/k or arrows: move        n/p or right/left: next/prev week":
        "Navigation   j/k ou flèches : bouger    n/p ou gauche/droite : semaine +/-",
    "             t: back to today           enter: details":
        "             t : aujourd'hui            entrée : détails",
    "Editing      a: add entry               e: edit title/time/room":
        "Édition      a : ajouter               e : modifier titre/heure/salle",
    "             o: edit notes in $EDITOR   space: done / work block done":
        "             o : notes dans $EDITOR    espace : fait / bloc de travail fait",
    "             d: delete (yours) / hide (intra) / dismiss (register?)":
        "             d : supprimer (perso) / masquer (intra) / ignorer (inscription)",
    "             H: show hidden             u: undo edits on an intra entry":
        "             H : voir masqués          u : annuler tes modifs sur une entrée intra",
    "Work plan    w: edit a project's tasks/hours (in $EDITOR)  T: start a task list":
        "Plan travail w : modifier tâches/heures d'un projet ($EDITOR)  T : créer une liste de tâches",
    "             l: log hours worked today  W: overview  g: suggest tasks from the GitHub repo":
        "             l : noter les heures  W : vue d'ensemble  g : suggérer des tâches depuis le dépôt GitHub",
    "Intra        r: activities to register  O: open on intra (to register)":
        "Intra        r : activités à s'inscrire  O : ouvrir sur l'intra (s'inscrire)",
    "             c: ask the assistant       s: sync now   q: quit":
        "             c : demander à l'assistant  s : synchroniser   q : quitter",
    "Colours: intra  mine  deadline  work block  register?":
        "Couleurs : intra  perso  rendu  bloc de travail  inscription ?",
    "(any key)": "(une touche)",
    # planner statuses / prompts
    "last sync: {when}   ? for help": "dernière synchro : {when}   ? pour l'aide",
    "never": "jamais",
    "{count} to register for - press r   (? for help)":
        "{count} à laquelle t'inscrire - appuie sur r   (? pour l'aide)",
    "could not read that date - try 2026-10-02 14:00, tomorrow 9:00, fri, 14:30":
        "date illisible - essaie 2026-10-02 14:00, tomorrow 9:00, fri, 14:30",
    "that is not a number of hours": "ce n'est pas un nombre d'heures",
    "Title: ": "Titre : ",
    "Start (e.g. 14:00, fri 10:00, 2026-10-02): ": "Début (ex. 14:00, fri 10:00, 2026-10-02) : ",
    "End (empty = none): ": "Fin (vide = aucune) : ",
    "Where (optional): ": "Où (facultatif) : ",
    "Start: ": "Début : ", "End: ": "Fin : ", "Where: ": "Où : ",
    "added '{title}'   (w on it turns it into a project with planned work time)":
        "ajouté '{title}'   (w dessus en fait un projet avec du temps de travail prévu)",
    "saved": "enregistré",
    " (your changes survive syncs; u to undo them)":
        " (tes modifs survivent aux synchros ; u pour les annuler)",
    "notes saved": "notes enregistrées",
    "dismissed - it won't remind you about this one again":
        "ignoré - tu ne seras plus rappelé pour celle-ci",
    "work blocks are planned for you: change the hours left with w, or log time with l":
        "les blocs de travail sont planifiés : change les heures avec w, ou note le temps avec l",
    "Delete '{title}'? (y/N) ": "Supprimer '{title}' ? (o/N) ",
    "deleted": "supprimé",
    "hidden (H shows hidden entries)": "masqué (H affiche les entrées masquées)",
    "unhidden": "réaffiché",
    "Drop your edits/notes on this entry? (y/N) ":
        "Abandonner tes modifs/notes sur cette entrée ? (o/N) ",
    "back to the intra version": "retour à la version de l'intra",
    "breaks are set with 'epiplan config' (edit the breaks list there)":
        "les pauses se règlent avec 'epiplan config' (modifie la liste breaks)",
    # details popup
    "Module": "Module", "Type": "Type", "Start": "Début", "End": "Fin",
    "Where": "Où", "Link": "Lien", "Source": "Source", "Edited": "Modifié", "Notes:": "Notes :",
    "intra.epitech.eu": "intra.epitech.eu", "your entry": "ton entrée",
    "planned work time": "temps de travail prévu",
    "recurring break (edit with: epiplan config)": "pause récurrente (modifier : epiplan config)",
    "not registered yet - O opens the intra page to register":
        "pas encore inscrit - O ouvre la page intra pour s'inscrire",
    # work plan
    "# One task per line, as:  HOURS  <what you'll do>   e.g.   3  build the model notebook":
        "# Une tâche par ligne :  HEURES  <ce que tu feras>   ex.   3  construire le notebook du modèle",
    "# Order = the order they'll be scheduled. Delete every line to plan by a single number instead.":
        "# L'ordre = l'ordre de planification. Supprime toutes les lignes pour planifier par un seul nombre.",
    "{n} tasks, {total} total": "{n} tâches, {total} au total",
    "# Put an x in front of a finished task (x 3  ...); syncing the repo ticks suggested ones itself.":
        "# Mets un x devant une tâche finie (x 3  ...) ; la synchro du dépôt coche seule les tâches suggérées.",
    "{n} tasks done": "{n} tâches faites",
    "task list cleared - press w again to set a single number of hours":
        "liste de tâches effacée - appuie encore sur w pour un simple nombre d'heures",
    "Hours left (0 = drop), or type a task list with T: ":
        "Heures restantes (0 = retirer), ou crée une liste de tâches avec T : ",
    "planned {h}; press w again to break it into named tasks":
        "{h} prévues ; appuie encore sur w pour les découper en tâches nommées",
    "select a project deadline or a work block first":
        "sélectionne d'abord un rendu de projet ou un bloc de travail",
    "pick a project deadline or a work block (w makes any entry a project)":
        "choisis un rendu de projet ou un bloc de travail (w transforme une entrée en projet)",
    "Hours worked on {title} today: ": "Heures travaillées sur {title} aujourd'hui : ",
    "logged {h} today": "{h} notées aujourd'hui",
    "only today's blocks can be ticked; for other work use l to log hours":
        "seuls les blocs d'aujourd'hui se cochent ; sinon utilise l pour noter les heures",
    "{title}: {left} left of {estimate}{guess}, due {deadline}":
        "{title} : {left} sur {estimate}{guess}, pour le {deadline}",
    " (guessed - press w to set it)": " (estimé - appuie sur w pour régler)",
    "  - NOT ENOUGH FREE TIME, {behind} short": "  - TEMPS LIBRE INSUFFISANT, {behind} de trop",
    "No projects yet.": "Aucun projet pour l'instant.",
    "Intra project deadlines appear here after a sync;":
        "les rendus de projet de l'intra apparaissent ici après une synchro ;",
    "press w on any entry to plan work time for it.":
        "appuie sur w sur une entrée pour lui planifier du temps de travail.",
    "Project work - {hours}, max {cap}/day": "Travail projet - {hours}, max {cap}/jour",
    "    today: {planned} planned, {done} done": "    aujourd'hui : {planned} prévues, {done} faites",
    "Nothing to register for right now.": "Rien à quoi s'inscrire pour l'instant.",
    "Activities in your modules you are not registered for":
        "Activités de tes modules auxquelles tu n'es pas inscrit",
    "(! = important; they also show in your week)":
        "(! = important ; elles apparaissent aussi dans ta semaine)",
    "Select one in the week view and press O to register on the intra, d to dismiss.":
        "Sélectionne-en une dans la semaine et appuie sur O pour t'inscrire, d pour ignorer.",
    "syncing with intra (a Chrome window opens if Microsoft needs you)...":
        "synchronisation avec l'intra (une fenêtre Chrome s'ouvre si Microsoft le demande)...",
    "synced {count} intra entries at {when}": "{count} entrées intra synchronisées à {when}",
    " - {count} important to register for (r)": " - {count} importantes à s'inscrire (r)",
    # cli login / main
    "Copy the 'user' cookie of intra.epitech.eu from your browser's dev tools.":
        "Copie le cookie 'user' de intra.epitech.eu depuis les outils dev de ton navigateur.",
    "user cookie (input hidden): ": "cookie user (saisie masquée) : ",
    "nothing entered": "rien saisi",
    "no graphical display - use: epiplan login --paste":
        "pas d'affichage graphique - utilise : epiplan login --paste",
    "A Chrome window is opening: log in with your Epitech Microsoft account":
        "Une fenêtre Chrome s'ouvre : connecte-toi avec ton compte Microsoft Epitech",
    "and tick 'Stay signed in' so future logins happen on their own.":
        "et coche 'Rester connecté' pour que les connexions suivantes soient automatiques.",
    "login was not completed": "connexion non terminée",
    "Logged in - synced {count} intra entries.": "Connecté - {count} entrées intra synchronisées.",
    "Logged in, but the first sync failed: {err}":
        "Connecté, mais la première synchro a échoué : {err}",
    "syncing with intra (logging in again if needed)...":
        "synchronisation avec l'intra (reconnexion si nécessaire)...",
    "synced {count} intra entries": "{count} entrées intra synchronisées",
    "added '{title}' on {start}": "ajouté '{title}' le {start}",
    "language set to {lang}": "langue réglée sur {lang}",
    # timed registration + deadline reminders
    "Registration open: {title}": "Inscription ouverte : {title}",
    "book your slot on the intra ({when})": "réserve ton créneau sur l'intra ({when})",
    "Register within ~{h}h: {title}": "À inscrire d'ici ~{h}h : {title}",
    "{when} - book your slot on the intra now": "{when} - réserve ton créneau sur l'intra maintenant",
    "Deadline in ~{h}h: {title}": "Rendu dans ~{h}h : {title}",
    "push / submit on the intra ({when})": "pousse / rends sur l'intra ({when})",
    # assistant / chatbot
    "I can only help with your Epitech intra: your schedule, deadlines, registrations and work plan. I can't write code or answer things outside the intra.":
        "Je ne peux t'aider que sur ton intra Epitech : ton emploi du temps, les rendus, les inscriptions et ton plan de travail. Je ne peux pas écrire de code ni répondre hors de l'intra.",
    "I only know your Epitech intra. Ask me about your schedule, deadlines, what to register for, or your work plan - or type 'help'.":
        "Je ne connais que ton intra Epitech. Pose-moi des questions sur ton emploi du temps, les rendus, les inscriptions ou ton plan de travail - ou tape 'help'.",
    "I answer questions about your Epitech intra only. Try:":
        "Je réponds uniquement sur ton intra Epitech. Essaie :",
    "  - what do I have today / tomorrow / this week?":
        "  - qu'est-ce que j'ai aujourd'hui / demain / cette semaine ?",
    "  - when is my next defense / review / keynote / exam?":
        "  - c'est quand ma prochaine soutenance / review / keynote / examen ?",
    "  - what should I work on now?   am I behind?":
        "  - sur quoi travailler maintenant ?   je suis en retard ?",
    "  - how many hours are left on Corewar?":
        "  - combien d'heures reste-t-il sur Corewar ?",
    "  - what do I need to register for?":
        "  - à quoi dois-je m'inscrire ?",
    "  - when is the Tardis deadline?":
        "  - c'est quand le rendu de Tardis ?",
    "  - how many credits do I have?  what's my GPA?  my grades?":
        "  - combien de crédits ai-je ?  quel est mon GPA ?  mes notes ?",
    "Nothing on {when}.": "Rien {when}.",
    "You have nothing to register for right now.": "Tu n'as rien à quoi t'inscrire pour l'instant.",
    "To register for:": "À inscrire :",
    " [slot - book a time]": " [créneau - à réserver]",
    "Project deadlines:": "Rendus de projet :",
    "No project deadlines.": "Aucun rendu de projet.",
    "{name}: {left} left, due {deadline}{behind}": "{name} : {left} restantes, pour le {deadline}{behind}",
    "  (SHORT {behind})": "  (MANQUE {behind})",
    "You're on track - no project is short on time.": "Tu es dans les temps - aucun projet ne manque de temps.",
    "Behind on: {names}": "En retard sur : {names}",
    "Today's plan:": "Plan d'aujourd'hui :",
    "Nothing planned for today.": "Rien de prévu aujourd'hui.",
    "Now/next: {block}": "Maintenant/à suivre : {block}",
    "Upcoming prep:": "Prépa à venir :",
    "No prep scheduled.": "Aucune prépa prévue.",
    "Your next {what}: {title}, {when}": "Ton/ta prochain(e) {what} : {title}, {when}",
    "No upcoming {what} found.": "Aucun(e) {what} à venir trouvé(e).",
    "Found:": "Trouvé :",
    "I'm not sure what you mean. I only know your intra - try 'help' to see what I can answer.":
        "Je ne suis pas sûr de comprendre. Je ne connais que ton intra - tape 'help' pour voir ce que je peux répondre.",
    "Ask about the intra: ": "Demande sur l'intra : ",
    "I don't have your intra profile yet - run a sync (press s) and ask again.":
        "Je n'ai pas encore ton profil intra - lance une synchro (touche s) et redemande.",
    "You currently have {n} credits.": "Tu as actuellement {n} crédits.",
    "You can obtain {n} more credits this semester (goal {goal}, you have {have}).":
        "Tu peux obtenir {n} crédits de plus ce semestre (objectif {goal}, tu en as {have}).",
    "I don't have your obtainable-credit total yet - run a sync (press s) and ask again.":
        "Je n'ai pas encore ton total de crédits à obtenir - lance une synchro (touche s) et redemande.",
    "GPA: {gpa}": "GPA : {gpa}",
    "Your GPA isn't in the intra profile.": "Ton GPA n'est pas dans le profil intra.",
    "Netsoul / log time: {v}": "Netsoul / temps de log : {v}",
    "not available": "non disponible",
    "No grades are available in your intra profile yet.":
        "Aucune note n'est disponible dans ton profil intra pour l'instant.",
    "Recent grades:": "Notes récentes :",
    "promo {p}": "promo {p}",
    "{n} credits": "{n} crédits",
    "GPA {gpa}": "GPA {gpa}",
    "Profile: {info}": "Profil : {info}",
    "Your intra profile is synced but has no summary fields.":
        "Ton profil intra est synchronisé mais n'a pas de champs résumés.",
    "opened the intra - I'll refresh your schedule once you've registered":
        "intra ouvert - j'actualiserai ton agenda dès que tu seras inscrit",
    "schedule updated - you're now registered": "agenda mis à jour - tu es maintenant inscrit",
    # repositories
    "{n} commits": "{n} commits", "last {when}": "dernier {when}",
    "{n} behind GitHub": "{n} en retard sur GitHub", "{n} to push": "{n} à pousser",
    "{n} TODOs": "{n} TODO", "missing: {what}": "manquant : {what}",
    "select a project deadline or work block first": "sélectionne d'abord un rendu de projet ou un bloc de travail",
    "no GitHub repo found for {name} - link it with: epiplan repo \"{name}\" <path-or-owner/repo>":
        "aucun dépôt GitHub trouvé pour {name} - lie-le avec : epiplan repo \"{name}\" <chemin-ou-owner/repo>",
    "syncing {name} with GitHub...": "synchronisation de {name} avec GitHub...",
    "{name}: {status}": "{name} : {status}",
    "# Suggested from your repo - keep the ones you want, edit the hours:":
        "# Suggéré depuis ton dépôt - garde celles que tu veux, ajuste les heures :",
    "linked {name} to {repo}": "{name} lié à {repo}",
    "no project matches \"{query}\"": "aucun projet ne correspond à « {query} »",
    "Repo: {status}": "Dépôt : {status}",
    # units / counts
    "important activity": "activité importante",
    "important activities": "activités importantes",
}

FR_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
FR_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre"]
FR_MONTHS_ABBR = ["janv", "févr", "mars", "avr", "mai", "juin", "juil", "août", "sept", "oct", "nov", "déc"]


def set_language(config):
    """Pick the UI language: the config's 'lang', then $EPIPLAN_LANG, then the system locale."""
    global LANG
    choice = config.get("lang") or os.environ.get("EPIPLAN_LANG")
    if not choice:
        env = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
        try:
            env = env or (locale.getdefaultlocale()[0] or "")
        except (ValueError, TypeError):
            pass
        choice = env
    LANG = "fr" if str(choice).lower().startswith("fr") else "en"


def _(text):
    """Translate a UI string to the current language (English strings are the source)."""
    return FR.get(text, text) if LANG == "fr" else text


def long_date(day):
    if LANG == "fr":
        return f"{FR_DAYS[day.weekday()]} {day.day:02d} {FR_MONTHS[day.month - 1]}"
    return day.strftime("%A %d %B")


def short_date(day):
    if LANG == "fr":
        return f"{day.day:02d} {FR_MONTHS_ABBR[day.month - 1]}"
    return day.strftime("%d %b")


def short_date_year(day):
    if LANG == "fr":
        return f"{day.day:02d} {FR_MONTHS_ABBR[day.month - 1]} {day.year}"
    return day.strftime("%d %b %Y")


def n_important(count):
    """A localised 'N important activity/activities'."""
    word = _("important activity") if count == 1 else _("important activities")
    return f"{count} {word}"


# ---------------------------------------------------------------- storage

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, data, private=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    if private:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_config():
    return {**DEFAULT_CONFIG, **load_json(CONFIG_FILE, {})}


def save_config(config):
    save_json(CONFIG_FILE, config, private=True)


def load_data():
    data = load_json(DATA_FILE, {})
    data.setdefault("items", {})
    data.setdefault("last_sync", None)
    data.setdefault("suggestions", {})
    data.setdefault("dismissed", [])
    data.setdefault("work", {})
    return data


def acquire_lock(handle):
    """Best-effort exclusive lock on an open file; released when the file closes."""
    try:
        if fcntl:
            fcntl.flock(handle, fcntl.LOCK_EX)
        elif msvcrt:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    except OSError:
        pass  # rare desktop contention: proceed without the lock rather than fail


@contextlib.contextmanager
def changing_data():
    """Load, change and save the planner under a lock, so the open planner and the
    background reminder job never overwrite each other's changes."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        acquire_lock(lock)
        data = load_data()
        yield data
        save_json(DATA_FILE, data)


def view(item):
    """An item as displayed: intra values with the user's overrides applied."""
    merged = dict(item)
    merged.update(item.get("overrides", {}))
    return merged


def edit_item(item, field, value):
    if item["source"] == "intra":
        item.setdefault("overrides", {})[field] = value
    else:
        item[field] = value


def new_local_item(title, start, end="", location=""):
    key = "local:" + uuid.uuid4().hex[:10]
    return key, {"id": key, "source": "local", "title": title, "start": start, "end": end,
                 "location": location, "notes": "", "done": False}


# ---------------------------------------------------------------- dates

def parse_when(text, base_day=None):
    """Accepts 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM', 'HH:MM', 'today 14:00', 'tomorrow', 'mon 10:00'."""
    text = text.strip().lower()
    base_day = base_day or date.today()
    day, clock = None, None
    for word in text.split():
        if word == "today":
            day = date.today()
        elif word == "tomorrow":
            day = date.today() + timedelta(days=1)
        elif word[:3] in ("mon", "tue", "wed", "thu", "fri", "sat", "sun") and not word[0].isdigit():
            target = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index(word[:3])
            day = date.today() + timedelta(days=(target - date.today().weekday()) % 7 or 7)
        elif ":" in word or "h" in word:
            hours, _, minutes = word.replace("h", ":").partition(":")
            clock = (int(hours), int(minutes or 0))
        else:
            day = date.fromisoformat(word)
    day = day or base_day
    if clock is None:
        return day.isoformat()
    return datetime(day.year, day.month, day.day, *clock).strftime("%Y-%m-%d %H:%M")


def day_of(stamp):
    return date.fromisoformat(stamp[:10])


def clock_of(stamp):
    return stamp[11:16] if len(stamp) > 10 else ""


def moment(stamp):
    return datetime.fromisoformat(stamp[:16])


def minutes_of(clock):
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def stamp_at(day, minutes):
    return f"{day.isoformat()} {minutes // 60:02d}:{minutes % 60:02d}"


def hours_text(hours):
    return f"{hours:g}h"


# ---------------------------------------------------------------- intra login

class IntraError(Exception):
    pass


class AuthError(IntraError):
    pass


LOGIN_URL = ("https://login.microsoftonline.com/common/oauth2/authorize?response_type=code"
             "&client_id=e05d4149-1624-4627-a5ba-7472a39e43ab"
             "&redirect_uri=https%3A%2F%2Fintra.epitech.eu%2Fauth%2Foffice365&state=%2F")
PROFILE_DIR = os.path.join(DATA_DIR, "browser-profile")


class NeedsYou(Exception):
    """Microsoft wants a password or MFA, so a silent login is impossible."""


def token_expiry(token):
    """The intra cookie is a JWT; read its expiry time, or None if it can't be read."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))["exp"]
    except (IndexError, ValueError, KeyError, TypeError):
        return None


def chrome_user_agent():
    """Headless Chrome announces itself as 'HeadlessChrome', which Microsoft may treat differently."""
    try:
        version = subprocess.run(["google-chrome", "--version"], capture_output=True, text=True).stdout
        major = version.split()[-1].split(".")[0]
        return f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    except (OSError, IndexError):
        return None


def click_through_microsoft(page):
    """Get past the Microsoft pages that only need a click when the session is remembered."""
    if "login.microsoftonline.com" not in page.url and "login.live.com" not in page.url:
        return
    if page.locator("input[type=password]:visible, input[type=email]:visible, input[name=otc]:visible").count():
        raise NeedsYou()
    remembered_account = page.locator("#tilesHolder [role=button]:not(#otherTile):visible, #tilesHolder [data-test-id]:visible")
    if remembered_account.count():
        remembered_account.first.click()
    elif page.locator("#idSIButton9:visible").count():  # "Stay signed in?" -> Yes
        page.locator("#idSIButton9").click()


def browser_login(visible):
    """Log in to the intra with a dedicated Chrome profile and return the 'user' cookie, or None."""
    try:
        from playwright.sync_api import sync_playwright, Error as PlaywrightError
    except ImportError:
        raise IntraError(_("automatic login needs Playwright (pip install playwright), or use: epiplan login --paste"))
    options = {"channel": "chrome", "headless": not visible}
    if not visible and chrome_user_agent():
        options["user_agent"] = chrome_user_agent()
    os.makedirs(PROFILE_DIR, mode=0o700, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(PROFILE_DIR, **options)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(LOGIN_URL)
            deadline = time.time() + (600 if visible else 45)
            while time.time() < deadline:
                for cookie in context.cookies(INTRA):
                    if cookie["name"] == "user":
                        return cookie["value"]
                if not visible:
                    click_through_microsoft(page)
                page.wait_for_timeout(1000)
        except NeedsYou:
            return None
        except PlaywrightError:
            return None  # window closed, or the page navigated away mid-check
        finally:
            try:
                context.close()
            except PlaywrightError:
                pass
    return None


def can_open_window():
    if IS_WINDOWS or IS_MAC:
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_in_browser(url):
    """Open a URL in the default browser, on any OS. Never raises if no opener is available."""
    try:
        if IS_WINDOWS:
            os.startfile(url)  # noqa: only defined on Windows
        elif IS_MAC:
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def default_editor():
    return os.environ.get("EDITOR") or ("notepad" if IS_WINDOWS else "nano")


def refresh_token(config, allow_window):
    token = browser_login(visible=False)
    if not token and allow_window and can_open_window():
        token = browser_login(visible=True)
    if not token:
        raise AuthError(_("could not log in automatically - run: epiplan login"))
    config["token"] = token
    save_config(config)
    return token


def get_token(config, allow_window):
    if os.environ.get("EPITECH_TOKEN"):
        return os.environ["EPITECH_TOKEN"]
    token = config.get("token")
    expiry = token_expiry(token) if token else None
    if token and (expiry is None or expiry > time.time() + 300):
        return token
    return refresh_token(config, allow_window)


def intra_get(token, path, params):
    params = dict(params, format="json")
    url = f"{INTRA}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={
        "Cookie": f"user={token}",
        "User-Agent": "epiplan/1.0",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise AuthError(_("intra refused the token"))
        raise IntraError(_("intra answered HTTP {code}").format(code=err.code))
    except urllib.error.URLError as err:
        raise IntraError(_("cannot reach intra: {reason}").format(reason=err.reason))
    body = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("//"))
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise AuthError(_("intra did not return JSON (not logged in)"))
    if isinstance(payload, dict) and "message" in payload and len(payload) <= 2:
        raise IntraError(_("intra: {message}").format(message=payload["message"]))
    return payload


# ---------------------------------------------------------------- intra data

def is_registered(event):
    return bool(event.get("event_registered")) or bool(event.get("rdv_group_registered")) \
        or bool(event.get("rdv_indiv_registered"))


def is_important(config, *texts):
    text = " ".join(t for t in texts if t).lower()
    return any(word in text for word in config["important"])


def event_fields(event):
    room = event.get("room") or {}
    return {
        "title": event.get("acti_title") or "?",
        "module": event.get("titlemodule") or event.get("codemodule") or "",
        "kind": event.get("type_title") or "",
        "start": event["start"][:16],
        "end": event["end"][:16],
        "location": (room.get("code") or "").rsplit("/", 1)[-1],
        "url": f"{INTRA}/module/{event['scolaryear']}/{event['codemodule']}/{event['codeinstance']}/{event['codeacti']}/",
    }


def event_key(event):
    return "intra:{scolaryear}:{codemodule}:{codeinstance}:{codeacti}:{codeevent}".format(**event)


def activity_key(event):
    return "{scolaryear}:{codemodule}:{codeinstance}:{codeacti}".format(**event)


def split_planning(planning, config):
    """Registered events become agenda items; open events of your modules become suggestions,
    one per activity (its next session), skipping activities you already have a session of."""
    registered, suggestions = {}, {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    planning = sorted(planning or [], key=lambda event: event["start"])
    taken_activities = {activity_key(event) for event in planning if is_registered(event)}
    for event in planning:
        key = "suggest:intra:" + activity_key(event)
        if is_registered(event):
            registered[event_key(event)] = event_fields(event)
        elif (event.get("module_registered") and event.get("allow_register", True) and event["start"][:16] > now
              and activity_key(event) not in taken_activities and key not in suggestions):
            fields = event_fields(event)
            seats = (event.get("room") or {}).get("seats")
            taken = event.get("total_students_registered")
            fields["full"] = bool(seats and taken and int(taken) >= int(seats))
            fields["important"] = is_important(config, fields["title"], fields["kind"], event.get("type_code"))
            suggestions[key] = fields
    return registered, suggestions


def split_board(board):
    """Registered projects become deadlines; projects you have not joined become suggestions."""
    deadlines, suggestions = {}, {}
    today = date.today().isoformat()
    for activity in board or []:
        end = (activity.get("end_acti") or "")[:16]
        if activity.get("type_acti_code") != "proj" or not end or end[:10] < today:
            continue
        code = activity.get("codeacti") or activity.get("title_link")
        title = activity.get("title") or activity.get("acti_title") or "?"
        fields = {
            "module": activity.get("title_module") or activity.get("codemodule") or "",
            "begin": (activity.get("begin_acti") or "")[:16],
            "url": INTRA + (activity.get("title_link") or "/"),
            "location": "",
        }
        if activity.get("registered"):
            deadlines[f"intra:proj:{code}"] = dict(fields, title=_("Deadline: {title}").format(title=title),
                                                    kind="Project deadline", start=end, end=end, project=title)
        else:
            start = max(fields["begin"][:10] or today, today)
            suggestions[f"suggest:proj:{code}"] = dict(fields, title=_("Join project {title}").format(title=title),
                                                       kind="Project", start=start, end="", important=True, full=False)
    return deadlines, suggestions


def extract_grades(raw):
    """Pull a flat list of module marks out of the profile, wherever the intra puts them."""
    source = raw.get("notes") or raw.get("grades") or raw.get("modules") or []
    if isinstance(source, dict):
        source = source.get("notes") or source.get("modules") or []
    grades = []
    for note in source if isinstance(source, list) else []:
        if not isinstance(note, dict):
            continue
        grade = note.get("note", note.get("grade", note.get("final_note", note.get("average"))))
        title = note.get("title") or note.get("titlemodule") or note.get("codemodule")
        if title and grade not in (None, ""):
            grades.append({"title": title, "grade": grade, "date": (note.get("date") or note.get("end") or "")[:10],
                           "credits": note.get("credits")})
    return grades


def _to_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def module_name_map(items):
    """Map a module code (G-CPE-100) to its readable name, from the synced planning items."""
    names = {}
    for fields in items:
        match = re.search(r"/module/\d+/([^/]+)/", fields.get("url", ""))
        if match and fields.get("module"):
            names[match.group(1)] = fields["module"]
    return names


def fetch_dashboard(token, names):
    """Read the intra homepage's current-semester credit breakdown - the same numbers the intra shows
    ('you have N, can obtain M, goal G'). Grade '-' means still obtainable; 'Echec' is a failure."""
    current = intra_get(token, "/", {}).get("current") or []
    obtainable = have = goal = 0.0
    grades = {}
    for module in current:
        credits = _to_number(module.get("credits"))
        grade = str(module.get("grade") or "").strip()
        goal = _to_number(module.get("credits_obj")) or goal
        if grade in ("-", ""):
            obtainable += credits              # not graded yet -> still earnable
        elif grade != "Echec":
            have += credits                    # a passing grade (A/B/C/D/Acquis)
        if grade and grade != "-" and credits > 0:
            code = module.get("code_module")
            grades[code] = {"title": names.get(code, code), "grade": grade, "credits": credits}
    tidy = lambda n: int(n) if n == int(n) else round(n, 1)
    order = {"Acquis": 0, "A": 1, "B": 2, "C": 3, "D": 4, "Echec": 9}
    return {"obtainable": tidy(obtainable), "goal": tidy(goal), "semester_have": tidy(have),
            "grades": sorted(grades.values(), key=lambda g: order.get(str(g["grade"]), 9))}


def fetch_profile(token):
    """The user's intra profile: credits, GPA, netsoul/log time, grades and everything else it holds."""
    raw = intra_get(token, "/user/", {})
    if not isinstance(raw, dict):
        return None
    return {
        "login": raw.get("login"),
        "name": raw.get("title") or raw.get("name"),
        "semester": raw.get("semester") or raw.get("studentyear") or raw.get("course_code"),
        "promo": raw.get("scolaryear") or raw.get("promo"),
        "credits": raw.get("credits"),
        "gpa": raw.get("gpa"),
        "netsoul": raw.get("nsstat"),
        "grades": extract_grades(raw),
        "raw": raw,  # keep the whole thing so nothing from the intra is lost
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def gpa_text(gpa):
    """Format the intra's GPA field (a list of {gpa, cycle}) into a readable string."""
    if isinstance(gpa, list):
        return ", ".join(f"{g.get('gpa', '?')} ({g.get('cycle', '')})".strip() for g in gpa if isinstance(g, dict)) \
            or str(gpa)
    return str(gpa)


def sync(config, allow_window=True):
    """Pull from the intra, logging in again by itself if the cookie has expired. Also refreshes
    linked GitHub repos (throttled) so project repos stay in step with the schedule. """
    try:
        count = sync_with_token(config, get_token(config, allow_window))
    except AuthError:
        if os.environ.get("EPITECH_TOKEN"):
            raise
        count = sync_with_token(config, refresh_token(config, allow_window))
    maybe_refresh_repos(config)
    return count


def sync_with_token(config, token):
    first = date.today() - timedelta(days=SYNC_DAYS_BEFORE)
    last = date.today() + timedelta(days=SYNC_DAYS_AFTER)
    window = {"start": first.isoformat(), "end": last.isoformat()}

    fetched, suggestions = split_planning(intra_get(token, "/planning/load", window), config)
    try:
        deadlines, project_suggestions = split_board(intra_get(token, "/module/board/", window))
        fetched.update(deadlines)
        suggestions.update(project_suggestions)
    except IntraError:
        pass  # deadlines are a bonus; the planning is what matters

    profile = dashboard = None
    try:
        profile = fetch_profile(token)  # credits, GPA, netsoul, and the rest of your intra profile
    except IntraError:
        pass
    if profile is not None:
        try:
            dashboard = fetch_dashboard(token, module_name_map(fetched.values()))
        except IntraError:
            pass

    with changing_data() as data:
        if profile is not None:
            previous = data.get("profile") or {}
            if dashboard is None:  # keep the last credit/grade figures if the homepage call failed
                for key in ("grades", "credits_obtainable", "credits_goal", "semester_credits"):
                    profile[key] = previous.get(key)
                profile["grades"] = profile["grades"] or []
            else:
                profile["grades"] = dashboard["grades"]
                profile["credits_obtainable"] = dashboard["obtainable"]
                profile["credits_goal"] = dashboard["goal"]
                profile["semester_credits"] = dashboard["semester_have"]
            data["profile"] = profile
        items = data["items"]
        for key, fields in fetched.items():
            item = items.setdefault(key, {"id": key, "source": "intra", "done": False, "overrides": {}})
            item.update(fields)
            item.pop("gone", None)

        for key, item in list(items.items()):
            in_window = first <= day_of(item["start"]) <= last
            if item["source"] == "intra" and in_window and key not in fetched:
                if item.get("overrides") or key in data["work"]:
                    item["gone"] = True  # keep the user's notes, but flag it
                else:
                    del items[key]

        for key, fields in suggestions.items():
            fields.update(id=key, source="suggest")
        data["suggestions"] = suggestions
        data["dismissed"] = [key for key in data["dismissed"] if key in suggestions]
        data["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    return len(fetched)


def open_suggestions(data, only_important=False):
    shown = [s for key, s in data["suggestions"].items()
             if key not in data["dismissed"] and s["start"] >= date.today().isoformat()
             and (s.get("important") or not only_important)]
    return sorted(shown, key=lambda s: (not s.get("important"), s["start"]))


# ---------------------------------------------------------------- work plan

def project_items(data):
    """Everything work gets planned for: intra project deadlines, plus any entry you gave an estimate."""
    for item in data["items"].values():
        merged = view(item)
        if merged.get("hidden") or merged.get("done") or merged.get("gone"):
            continue
        if merged.get("kind") == "Project deadline" or data["work"].get(merged["id"], {}).get("estimate") is not None:
            yield dict(merged, verb="Work on", earliest=date.today().isoformat())


def prep_category(config, *texts):
    """Which kind of prep an event needs (defense/keynote/review/follow-up), or None."""
    text = " ".join(t for t in texts if t).lower()
    for category in config["prep_hours"]:
        if category in text:
            return category
    return None


def is_slot_activity(config, *texts):
    """Whether an activity needs a booked time slot (defense, review, keynote, bootstrap, kick-off...)."""
    text = " ".join(t for t in texts if t).lower()
    return any(word in text for word in config.get("register_activities", []))


def prep_targets(data, config):
    """A prep task before each upcoming review, keynote or defense you are registered for.
    Its deadline is the event; prep is placed in the days just before it."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for item in data["items"].values():
        merged = view(item)
        category = prep_category(config, merged.get("kind"), merged["title"])
        if not category or merged["start"] <= now or merged.get("hidden") or merged.get("gone"):
            continue
        prep_id = "prep:" + merged["id"]
        earliest = day_of(merged["start"]) - timedelta(days=config["prep_lead_days"])
        yield dict(merged, id=prep_id, verb="Prep for", event=merged["title"],
                   earliest=max(earliest, date.today()).isoformat(),
                   default_estimate=config["prep_hours"][category])


def guessed_hours(project):
    """Prep tasks default to the per-type hours; projects assume 1 hour per day of length (4-20h)."""
    if project.get("default_estimate") is not None:
        return project["default_estimate"]
    begin = project.get("begin") or ""
    length = (day_of(project["start"]) - day_of(begin)).days if begin else 14
    return max(4, min(20, length))


def task_segments(work, spent_hours):
    """The project's remaining tasks as [title, half-hours], in order, after the hours already
    worked are subtracted from the earliest tasks. Ticked tasks are skipped (the hours worked count
    towards them first). None if the project has no task list."""
    tasks = work.get("tasks") or []
    if not tasks:
        return None
    spent = max(0, round((spent_hours - done_task_hours(tasks)) * 2))
    segments = []
    for task in (t for t in tasks if not t.get("done")):
        half = round(task["hours"] * 2)
        take = min(half, spent)
        half -= take
        spent -= take
        if half > 0:
            segments.append([task["title"], half])
    return segments or [[tasks[-1]["title"], 0]]


def done_task_hours(tasks):
    return sum(t["hours"] for t in tasks if t.get("done"))


def work_status(data, project):
    work = data["work"].get(project["id"], {})
    logged = work.get("logged", {})
    tasks = work.get("tasks") or []
    estimate = sum(t["hours"] for t in tasks) if tasks else work.get("estimate")
    guessed = estimate is None
    if guessed:
        estimate = guessed_hours(project)
    today = date.today().isoformat()
    ticked = done_task_hours(tasks)  # a ticked task counts as worked, even if its hours weren't logged
    done = max(ticked, sum(logged.values()))
    done_before_today = max(ticked, sum(h for day, h in logged.items() if day < today))
    return {
        "estimate": estimate,
        "guessed": guessed,
        "done": done,
        "done_today": logged.get(today, 0),
        "left": max(0, estimate - done),
        "left_this_morning": max(0, estimate - done_before_today),
    }


def free_slots(window, busy):
    slots = [window]
    for start, end in sorted(busy):
        remaining = []
        for slot_start, slot_end in slots:
            if end <= slot_start or start >= slot_end:
                remaining.append((slot_start, slot_end))
                continue
            if start > slot_start:
                remaining.append((slot_start, start))
            if end < slot_end:
                remaining.append((end, slot_end))
        slots = remaining
    return slots


def day_activity_text(data):
    """The titles and kinds of the fixed-time activities on each day, for the breaks' if_activity."""
    text = defaultdict(str)
    for merged in [view(item) for item in data["items"].values()] + open_suggestions(data, only_important=True):
        if merged.get("hidden") or merged.get("gone") or not clock_of(merged["start"]):
            continue
        if merged.get("kind") == "Project deadline":
            continue
        text[day_of(merged["start"])] += " " + merged.get("title", "") + " " + merged.get("kind", "")
    return text


def break_applies(period, day, day_text):
    if day.weekday() not in parse_days(period["days"]):
        return False
    keyword = period.get("if_activity", "").lower()
    return not keyword or keyword in day_text[day].lower()


def break_blocks(data, config, first, last):
    """Visible entries for the recurring breaks that carry a label, one per matching day."""
    day_text = day_activity_text(data)
    blocks, day = [], first
    while day <= last:
        for period in config.get("breaks", []):
            if period.get("label") and break_applies(period, day, day_text):
                start = minutes_of(period["start"])
                blocks.append({"id": f"break:{day}:{start}", "source": "break", "title": _(period["label"]),
                               "start": stamp_at(day, start), "end": stamp_at(day, minutes_of(period["end"]))})
        day += timedelta(days=1)
    return blocks


def busy_times(data, config):
    """Minutes already taken, per day, by your activities and entries, plus your recurring breaks.
    Important activities you still have to register for count too, so work never lands on them."""
    busy = defaultdict(list)
    for merged in [view(item) for item in data["items"].values()] + open_suggestions(data, only_important=True):
        if merged.get("hidden") or merged.get("gone") or not clock_of(merged["start"]):
            continue
        if merged.get("kind") == "Project deadline":
            continue
        start = minutes_of(clock_of(merged["start"]))
        end = start + 60
        if merged.get("end") and clock_of(merged["end"]):
            end = minutes_of(clock_of(merged["end"])) if merged["end"][:10] == merged["start"][:10] else 24 * 60
        busy[day_of(merged["start"])].append((start, max(end, start + 30)))

    day_text = day_activity_text(data)
    today = date.today()
    for offset in range(SYNC_DAYS_AFTER + 1):
        day = today + timedelta(days=offset)
        for period in config.get("breaks", []):
            if break_applies(period, day, day_text):
                busy[day].append((minutes_of(period["start"]), minutes_of(period["end"])))
    return busy


def today_start(data):
    anchor = data.get("today_from") or {}
    if anchor.get("day") == date.today().isoformat():
        return anchor["minutes"]
    return rounded_now()


def rounded_now():
    now = datetime.now()
    return -(-(now.hour * 60 + now.minute) // 30) * 30


def pin_today_start():
    """Remember when today's plan was first made, so today's blocks don't slide as time passes."""
    if (load_data().get("today_from") or {}).get("day") == date.today().isoformat():
        return
    with changing_data() as data:
        if (data.get("today_from") or {}).get("day") != date.today().isoformat():
            data["today_from"] = {"day": date.today().isoformat(), "minutes": rounded_now()}


def build_plan(data, config):
    """Spread each project's remaining hours evenly over the days before its deadline, earliest
    deadline first, inside your free time. The plan for today is computed from what was left this
    morning, so today's blocks stay put while you tick them off. Today starts when you first looked."""
    first, _sep, last = config["work_hours"].partition("-")
    window = (minutes_of(first), minutes_of(last))
    daily_cap = int(config["max_work_hours_per_day"] * 2)  # in half hours
    max_block = max(1, int(config["max_block_hours"] * 2))
    today = date.today()
    windows = defaultdict(lambda: window)
    windows[today] = (max(window[0], today_start(data)), window[1])
    busy = busy_times(data, config)
    used = defaultdict(int)
    blocks, summary = [], {}

    def place(project, day, half_hours, segments, default_label):
        """Fill up to half_hours of free time on a day. Each block stays within one task, so its
        title is that task; without a task list every block carries the generic label."""
        placed = 0
        deadline = moment(project["start"])
        window_start, window_end = windows[day]
        if day == deadline.date():
            window_end = min(window_end, deadline.hour * 60 + deadline.minute)
        while placed < half_hours:
            while segments and segments[0][1] <= 0:
                segments.pop(0)
            want = min(half_hours - placed, max_block)
            if segments:
                want = min(want, segments[0][1])
            slot = next(((start, end) for start, end in free_slots((window_start, window_end), busy[day])
                         if (end - start) // 30 >= min(want, 2)), None)  # skip gaps under 1h
            if not slot:
                break
            start = slot[0]
            take = min(want, (slot[1] - start) // 30)
            block_end = start + take * 30
            busy[day] += [(start, block_end), (block_end, block_end + 30)]  # block, then a break
            used[day] += take
            placed += take
            title = segments[0][0] if segments else default_label
            if segments:
                segments[0][1] -= take
            blocks.append({
                "id": f"plan:{project['id']}:{day}:{start}", "source": "plan", "project": project["id"],
                "title": title, "module": project.get("module", ""),
                "start": stamp_at(day, start), "end": stamp_at(day, block_end), "hours": take / 2,
            })
        return placed

    targets = list(project_items(data)) + list(prep_targets(data, config))
    for project in sorted(targets, key=lambda p: p["start"]):
        status = work_status(data, project)
        deadline = moment(project["start"])
        earliest = max(today, date.fromisoformat(project["earliest"]))
        days = [earliest + timedelta(days=n) for n in range((deadline.date() - earliest).days + 1)]
        if len(days) > 1 and deadline.hour * 60 + deadline.minute < window[0] + 60:
            days.pop()  # a deadline early in the morning leaves no time to work that day
        if project.get("event"):
            default_label = _("Prep for {name}").format(name=project["event"])
        else:
            name = project.get("project") or project["title"]
            default_label = _("Prep for {name}").format(name=name) if project.get("verb") == "Prep for" \
                else _("Work on {name}").format(name=name)
        spent_before_today = status["estimate"] - status["left_this_morning"]
        segments = task_segments(data["work"].get(project["id"], {}), spent_before_today)
        left = round(status["left_this_morning"] * 2)
        for index, day in enumerate(days):
            share = -(-left // (len(days) - index))
            left -= place(project, day, min(share, daily_cap - used[day]), segments, default_label)
        for day in days:
            if left > 0:
                left -= place(project, day, min(left, daily_cap - used[day]), segments, default_label)
        label = _("Prep: {name}").format(name=project["event"]) if project.get("event") \
            else project.get("project") or project["title"]
        summary[project["id"]] = dict(status, title=label, deadline=project["start"], behind=left / 2)

    for project_id, status in summary.items():
        to_tick = status["done_today"]
        for block in sorted((b for b in blocks if b["project"] == project_id and day_of(b["start"]) == today),
                            key=lambda b: b["start"]):
            block["done"] = to_tick >= block["hours"]
            to_tick -= block["hours"] if block["done"] else 0
    return blocks, summary


# ---------------------------------------------------------------- agenda

def agenda_between(data, config, first, last, show_hidden=False, plan=None):
    """Your items, work blocks and important registration reminders between two days."""
    blocks, _ = plan or build_plan(data, config)
    shown = [view(item) for item in data["items"].values()]
    shown = [m for m in shown if show_hidden or not m.get("hidden")]
    shown += blocks + open_suggestions(data, only_important=True) + break_blocks(data, config, first, last)
    return sorted((m for m in shown if first <= day_of(m["start"]) <= last),
                  key=lambda m: (m["start"], m["title"]))


def describe(merged, summary=None):
    if merged["source"] == "suggest":
        box = "[?]"
    elif merged["source"] == "break":
        box = "···"
    else:
        box = "[x]" if merged.get("done") else "[ ]"
    times = clock_of(merged["start"])
    if merged.get("end") and clock_of(merged["end"]) and merged["end"] != merged["start"]:
        times += "-" + clock_of(merged["end"])
    times = (times or _("all day")).ljust(11)
    title = merged["title"]
    if merged["source"] == "suggest":
        title = _("Register? {title}").format(title=title)
    if merged["source"] == "plan":
        title += f" ({hours_text(merged['hours'])})"
    elif merged.get("module"):
        title += f"  ({merged['module']})"
    if merged.get("location"):
        title += f"  @ {merged['location']}"
    flags = ""
    status = (summary or {}).get(merged["id"])
    if status:
        flags += _("  - {left} left").format(left=hours_text(status["left"]))
        flags += _(" (guess, w to set)") if status["guessed"] else ""
        if status["behind"]:
            flags += _("  NOT ENOUGH TIME: {behind} short").format(behind=hours_text(status["behind"]))
    if merged.get("full"):
        flags += _(" (full)")
    if merged.get("notes"):
        flags += " *"
    if merged.get("gone"):
        flags += _(" (removed on intra)")
    if merged.get("hidden"):
        flags += _(" (hidden)")
    return f"{box} {times} {title}{flags}"


SOURCE_LABELS = {"intra": "intra   ", "local": "mine    ", "plan": "work    ",
                 "suggest": "register", "break": "break   "}


def print_agenda(data, config, days):
    today = date.today()
    plan = build_plan(data, config)
    current = None
    for merged in agenda_between(data, config, today, today + timedelta(days=days - 1), plan=plan):
        day = day_of(merged["start"])
        if day != current:
            current = day
            print(f"\n{long_date(day)}")
        print(f"  {_(SOURCE_LABELS[merged['source']])} {describe(merged, plan[1])}")
    for status in [s for s in plan[1].values() if s["behind"]]:
        print("\n" + _("! not enough free time for {title}: {behind} short before {deadline}").format(
            title=status["title"], behind=hours_text(status["behind"]), deadline=status["deadline"]))
    pending = open_suggestions(data, only_important=True)
    if pending:
        print("\n" + _("{count} to register for - see 'r' in epiplan").format(count=n_important(len(pending))))


# ---------------------------------------------------------------- assistant (intra-only Q&A)

# A small, offline assistant that answers questions about the user's own intra data. It has no
# access to anything but the synced planner, so it cannot go off-topic; and it deliberately declines
# coding and general-knowledge questions.

ASSISTANT_HELP_LINES = [
    "I answer questions about your Epitech intra only. Try:",
    "  - what do I have today / tomorrow / this week?",
    "  - when is my next defense / review / keynote / exam?",
    "  - what should I work on now?   am I behind?",
    "  - how many hours are left on Corewar?",
    "  - what do I need to register for?",
    "  - when is the Tardis deadline?",
    "  - how many credits do I have?  what's my GPA?  my grades?",
]

CODING_HINTS = ["write code", "write a program", "write me", "debug", "fix my code", "fix this",
                "code for", "implement", "a function that", "segfault", "compile error",
                "syntax error", "stack trace", "how do i code", "how to code", "program that",
                "give me code", "snippet", "write a script",
                # French
                "écris", "écrire", "fonction python", "fonction en c", "un algorithme", "algorithme pour",
                "compil", "débog", "corrige mon code", "code pour", "un script"]

OFF_TOPIC_HINTS = ["weather", "news", "recipe", "who is", "what is the capital", "translate",
                   "joke", "story", "president", "météo", "actualité", "recette", "blague"]

def _looks_like_coding(q):
    return any(h in q for h in CODING_HINTS)


WEEKDAY_WORDS = {}


def _init_weekday_words():
    en = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, name in enumerate(en):
        WEEKDAY_WORDS[name] = i
        WEEKDAY_WORDS[name[:3]] = i
    for i, name in enumerate(FR_DAYS):
        WEEKDAY_WORDS[name] = i


_init_weekday_words()


def _has(q, *words):
    return any(w in q for w in words)


def _assistant_range(q, today):
    """Turn a date phrase in the question into (first, last, label), or None."""
    if _has(q, "tomorrow", "demain"):
        d = today + timedelta(days=1)
        return d, d, long_date(d)
    if _has(q, "today", "aujourd", "tonight", "ce soir"):
        return today, today, long_date(today)
    if _has(q, "week", "semaine"):
        if _has(q, "next", "coming", "following", "prochain", "qui vient", "suivante"):
            mon = today - timedelta(days=today.weekday()) + timedelta(days=7)
        else:
            mon = today - timedelta(days=today.weekday())
        return mon, mon + timedelta(days=6), f"{long_date(mon)} - {long_date(mon + timedelta(days=6))}"
    if _has(q, "weekend"):
        sat = today - timedelta(days=today.weekday()) + timedelta(days=5)
        return sat, sat + timedelta(days=1), "weekend"
    for word, weekday in WEEKDAY_WORDS.items():
        if re.search(r"\b" + re.escape(word) + r"\b", q):
            d = today + timedelta(days=(weekday - today.weekday()) % 7)
            return d, d, long_date(d)
    match = re.search(r"\d{4}-\d{2}-\d{2}", q)
    if match:
        try:
            d = date.fromisoformat(match.group())
            return d, d, long_date(d)
        except ValueError:
            pass
    return None


def _fmt_line(merged, summary):
    return describe(merged, summary)


def _agenda_answer(data, config, first, last, label, plan):
    rows = agenda_between(data, config, first, last, plan=plan)
    if not rows:
        return _("Nothing on {when}.").format(when=label)
    out, current = [], None
    for merged in rows:
        day = day_of(merged["start"])
        if day != current:
            current = day
            out.append(long_date(day) + ":")
        out.append("  " + _fmt_line(merged, plan[1]))
    return "\n".join(out)


def _deadlines_answer(plan, now):
    rows = [(pid, s) for pid, s in plan[1].items() if not pid.startswith("prep:")]
    rows.sort(key=lambda kv: kv[1]["deadline"])
    if not rows:
        return _("No project deadlines.")
    lines = [_("Project deadlines:")]
    for _pid, s in rows:
        behind = _("  (SHORT {behind})").format(behind=hours_text(s["behind"])) if s["behind"] else ""
        lines.append("  " + _("{name}: {left} left, due {deadline}{behind}").format(
            name=s["title"], left=hours_text(s["left"]), deadline=s["deadline"], behind=behind))
    return "\n".join(lines)


def _register_answer(data, config, now):
    pending = open_suggestions(data)
    if not pending:
        return _("You have nothing to register for right now.")
    lines = [_("To register for:")]
    for s in pending:
        slot = _(" [slot - book a time]") if is_slot_activity(config, s.get("kind"), s.get("title", "")) else ""
        module = f"  ({s.get('module', '')})" if s.get("module") else ""
        lines.append(f"  {s['start'][:16]}  {s['title']}{module}{slot}")
    return "\n".join(lines)


def _work_answer(data, config, plan, q, now):
    blocks, summary = plan
    today = date.today()
    projects = {pid: s for pid, s in summary.items() if not pid.startswith("prep:")}

    for pid, s in projects.items():  # "how many hours left on <project>"
        words = [w for w in re.split(r"\W+", s["title"].lower()) if len(w) > 3]
        if any(w in q for w in words):
            behind = _("  (SHORT {behind})").format(behind=hours_text(s["behind"])) if s["behind"] else ""
            return _("{name}: {left} left, due {deadline}{behind}").format(
                name=s["title"], left=hours_text(s["left"]), deadline=s["deadline"], behind=behind)

    if _has(q, "behind", "retard", "enough time", "assez de temps"):
        late = [s["title"] for s in projects.values() if s["behind"]]
        return _("Behind on: {names}").format(names=", ".join(late)) if late \
            else _("You're on track - no project is short on time.")

    todays = sorted((b for b in blocks if day_of(b["start"]) == today), key=lambda b: b["start"])
    if _has(q, "now", "maintenant", "next", "prochain"):
        nxt = next((b for b in todays if moment(b["end"]) > now and not b.get("done")), None)
        if nxt:
            return _("Now/next: {block}").format(block=describe(nxt))
    if not todays:
        return _("Nothing planned for today.")
    return _("Today's plan:") + "\n" + "\n".join("  " + describe(b) for b in todays)


def _prep_answer(plan):
    preps = sorted((b for b in plan[0] if b["project"].startswith("prep:")), key=lambda b: b["start"])
    if not preps:
        return _("No prep scheduled.")
    return _("Upcoming prep:") + "\n" + "\n".join(
        f"  {long_date(day_of(b['start']))} {clock_of(b['start'])}  {b['title']}" for b in preps[:8])


NEXT_WORDS = {
    "defense": ["defense", "defence", "soutenance"], "review": ["review"], "keynote": ["keynote"],
    "follow-up": ["follow-up", "follow up", "followup"], "bootstrap": ["bootstrap"],
    "kick-off": ["kick-off", "kickoff", "kick off"], "exam": ["exam", "examen"],
    "deadline": ["deadline", "due", "rendu"], "stumper": ["stumper"],
}


def _next_answer(data, config, q, now, plan):
    what = next((label for label, words in NEXT_WORDS.items() if _has(q, *words)), None)
    if what == "deadline":
        rows = sorted((s for pid, s in plan[1].items() if not pid.startswith("prep:")),
                      key=lambda s: s["deadline"])
        return _("Your next {what}: {title}, {when}").format(what=_(what), title=rows[0]["title"], when=rows[0]["deadline"]) \
            if rows else _("No upcoming {what} found.").format(what=_(what))
    now_s = now.strftime("%Y-%m-%d %H:%M")
    pool = [view(i) for i in data["items"].values()] + list(data["suggestions"].values())
    hits = [m for m in pool if m["start"] > now_s and not m.get("gone") and not m.get("hidden")
            and (what is None or _has((m.get("title", "") + " " + m.get("kind", "")).lower(), *NEXT_WORDS.get(what, [])))]
    if not hits:
        return _("No upcoming {what} found.").format(what=_(what) if what else "activity")
    m = min(hits, key=lambda m: m["start"])
    where = f" @ {m['location']}" if m.get("location") else ""
    return _("Your next {what}: {title}, {when}").format(
        what=_(what) if what else "", title=m["title"], when=m["start"][:16] + where).replace("  ", " ").strip()


def _profile_answer(data, q):
    """Answer credits / GPA / grades / netsoul / profile questions from the synced intra profile."""
    profile = data.get("profile")
    if not profile:
        return _("I don't have your intra profile yet - run a sync (press s) and ask again.")
    if _has(q, "credit", "crédit", "ects"):
        if _has(q, "eligible", "eligib", "éligib", "available", "can i earn", "can i get", "possible",
                "on offer", "worth", "registered for", "this semester", "this year", "obtenir", "disponible"):
            ob = profile.get("credits_obtainable")
            return _("You can obtain {n} more credits this semester (goal {goal}, you have {have}).").format(
                n=ob, goal=profile.get("credits_goal", "?"), have=profile.get("credits", "?")) if ob is not None \
                else _("I don't have your obtainable-credit total yet - run a sync (press s) and ask again.")
        return _("You currently have {n} credits.").format(n=profile.get("credits", "?"))
    if _has(q, "gpa", "average", "moyenne"):
        return _("GPA: {gpa}").format(gpa=gpa_text(profile.get("gpa"))) if profile.get("gpa") is not None \
            else _("Your GPA isn't in the intra profile.")
    if _has(q, "netsoul", "net soul", "log time", "logtime"):
        ns = profile.get("netsoul")
        if isinstance(ns, dict):
            ns = ", ".join(f"{k}: {v}" for k, v in ns.items())
        return _("Netsoul / log time: {v}").format(v=ns or _("not available"))
    if _has(q, "grade", "mark", "note", "résultat", "resultat"):
        grades = profile.get("grades") or []
        if not grades:
            return _("No grades are available in your intra profile yet.")
        lines = [_("Recent grades:")]
        for g in grades[:15]:
            when = f"  {g['date']}" if g.get("date") else ""
            lines.append(f"  {g['grade']:<4} {g['title']}{when}")
        return "\n".join(lines)
    # generic profile summary
    bits = []
    if profile.get("name"):
        bits.append(profile["name"])
    if profile.get("promo"):
        bits.append(_("promo {p}").format(p=profile["promo"]))
    if profile.get("credits") is not None:
        bits.append(_("{n} credits").format(n=profile["credits"]))
    if profile.get("gpa") is not None:
        bits.append(_("GPA {gpa}").format(gpa=gpa_text(profile["gpa"])))
    return _("Profile: {info}").format(info=" · ".join(str(b) for b in bits)) if bits else \
        _("Your intra profile is synced but has no summary fields.")


def _search_answer(data, config, q, plan, now):
    words = [w for w in re.split(r"\W+", q) if len(w) > 3
             and w not in ("when", "what", "where", "have", "with", "about", "quand", "quoi")]
    pool = [view(i) for i in data["items"].values()] + list(data["suggestions"].values())
    hits = [m for m in pool if not m.get("gone") and any(w in m.get("title", "").lower() for w in words)]
    if not hits:
        return None
    hits.sort(key=lambda m: m["start"])
    return _("Found:") + "\n" + "\n".join(f"  {m['start'][:16]}  {m['title']}" for m in hits[:8])


def assistant_answer(data, config, question):
    """Answer a natural-language question about the intra, or decline if it's out of scope."""
    q = (question or "").strip().lower()
    if not q or _has(q, "help", "aide", "what can you"):
        return "\n".join(_(line) for line in ASSISTANT_HELP_LINES)
    if _looks_like_coding(q) or _has(q, *OFF_TOPIC_HINTS):
        return _("I can only help with your Epitech intra: your schedule, deadlines, registrations and work plan. I can't write code or answer things outside the intra.")

    if _has(q, "credit", "crédit", "credit", "ects", "gpa", "average", "moyenne", "grade", "mark",
            "note", "résultat", "resultat", "netsoul", "net soul", "log time", "logtime", "profile",
            "profil", "who am i", "qui suis"):
        answer = _profile_answer(data, q)
        if answer:
            return answer

    plan = build_plan(data, config)
    now = datetime.now()
    if _has(q, "deadline", "due", "rendu", "rendre"):
        return _deadlines_answer(plan, now)
    if _has(q, "register", "registration", "sign up", "inscri", "créneau", "creneau", "slot", "book"):
        return _register_answer(data, config, now)
    # A day/week phrase ("tomorrow", "next week", "friday"...) means "what's my schedule then" - check
    # it before the plan/next intents so "my planning for tomorrow" isn't caught by the word "plan".
    date_range = _assistant_range(q, date.today())
    if date_range:
        return _agenda_answer(data, config, date_range[0], date_range[1], date_range[2], plan)
    if _has(q, "next", "prochain"):
        return _next_answer(data, config, q, now, plan)
    if _has(q, "work on", "should i do", "should i work", "to do", "plan", "travail", "behind",
            "retard", "hours left", "hours are left", "heures", "progress", "avanc"):
        return _work_answer(data, config, plan, q, now)
    if _has(q, "prep", "prépa", "prepa", "rehearse", "réviser"):
        return _prep_answer(plan)
    found = _search_answer(data, config, q, plan, now)
    if found:
        return found
    return _("I'm not sure what you mean. I only know your intra - try 'help' to see what I can answer.")


# ---------------------------------------------------------------- repositories

# Links each intra project to its GitHub repo (a local clone, or an owner/name slug it clones into a
# cache), fetches from GitHub, reads the code, suggests tasks, and ticks off the suggested tasks the
# code shows are done. Runs on 'g' / 'epiplan repos' and, throttled, on the regular sync.

REPO_CACHE = os.path.join(DATA_DIR, "repos")


GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true",
               GIT_SSH_COMMAND="ssh -oBatchMode=yes -oStrictHostKeyChecking=accept-new")


def run_git(path, *args, timeout=30):
    """Run a git command non-interactively (never prompts, so it can't hang the app)."""
    try:
        result = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True,
                                timeout=timeout, env=GIT_ENV)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def is_git_repo(path):
    return bool(path) and os.path.isdir(path) and \
        (os.path.isdir(os.path.join(path, ".git")) or run_git(path, "rev-parse", "--is-inside-work-tree") == "true")


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def project_name_of(project):
    """A clean project name for matching (drops a 'Deadline:' / 'Rendu :' prefix)."""
    name = project.get("project") or project.get("title") or ""
    return re.sub(r"^\s*(deadline|rendu)\s*[:\-]\s*", "", name, flags=re.I).strip()


def find_local_repo(project, config):
    """Search the configured directories for a git clone whose folder name matches the project."""
    needle = _norm(project_name_of(project))
    if len(needle) < 3:
        return None
    for base in config.get("repo_search_dirs", []):
        base = os.path.expanduser(base)
        if not os.path.isdir(base):
            continue
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            continue
        for entry in entries:
            path = os.path.join(base, entry)
            if needle in _norm(entry) and is_git_repo(path):
                return path
    return None


def clone_repo(slug, dest):
    for command in (["gh", "repo", "clone", slug, dest],
                    ["git", "clone", "--quiet", f"git@github.com:{slug}.git", dest],
                    ["git", "clone", "--quiet", f"https://github.com/{slug}.git", dest]):
        try:
            if subprocess.run(command, capture_output=True, timeout=180, env=GIT_ENV).returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def repo_checkout(repo):
    """A local working directory for the linked repo: the path itself, or a cached clone of a slug."""
    if not repo:
        return None
    if os.path.isdir(repo):
        return repo
    if "/" in repo and not os.path.isabs(repo):  # owner/name GitHub slug
        dest = os.path.join(REPO_CACHE, repo.replace("/", "_"))
        if is_git_repo(dest):
            run_git(dest, "pull", "--quiet")
            return dest
        os.makedirs(REPO_CACHE, exist_ok=True)
        return dest if clone_repo(repo, dest) else None
    return None


def analyze_repo(path):
    """Read a checked-out repo and summarise progress + things left to do."""
    run_git(path, "fetch", "--quiet")  # reflect GitHub; harmless if offline
    files = run_git(path, "ls-files").splitlines()
    lower = [f.lower() for f in files]
    counts = run_git(path, "rev-list", "--left-right", "--count", "HEAD...@{upstream}").split()
    ahead = int(counts[0]) if len(counts) == 2 else 0
    behind = int(counts[1]) if len(counts) == 2 else 0

    skip_dirs = ("node_modules/", ".venv/", "vendor/", "dist/", "build/")
    skip_ext = (".ipynb", ".lock", ".map", ".min.js", ".csv", ".json", ".svg", ".pdf")
    todos = []
    for line in run_git(path, "grep", "-nIE", r"\b(TODO|FIXME)\b").splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        fpath, text = parts[0], parts[2]
        if fpath.startswith(skip_dirs) or fpath.lower().endswith(skip_ext):
            continue
        text = re.sub(r"^[\s/*#;>-]+", "", text)
        text = re.sub(r"\b(TODO|FIXME)\b[:\s-]*", "", text, count=1).strip()
        if not text or len(text) > 100 or (len(text) > 24 and " " not in text):  # skip base64/minified noise
            continue
        todos.append((fpath, text))

    exts = {os.path.splitext(f)[1] for f in files}
    missing = []
    if not any(f.startswith("readme") for f in lower):
        missing.append("readme")
    if (exts & {".c", ".cpp", ".cc", ".h", ".hpp"}) and not any(os.path.basename(f) == "makefile" for f in lower):
        missing.append("makefile")
    if files and not any("test" in f for f in lower):
        missing.append("tests")

    return {
        "path": path,
        "remote": run_git(path, "remote", "get-url", "origin"),
        "branch": run_git(path, "rev-parse", "--abbrev-ref", "HEAD"),
        "commits": run_git(path, "rev-list", "--count", "HEAD") or "0",
        "last_commit": run_git(path, "log", "-1", "--format=%cd", "--date=short"),
        "ahead": ahead, "behind": behind, "files": len(files),
        "todos": todos, "missing": missing,
        "synced_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


MISSING_TASK = {"readme": ("Write the README", 1), "makefile": ("Add a Makefile (re/clean/fclean)", 1),
                "tests": ("Add tests", 2)}


def suggest_tasks(analysis, limit=12):
    """Turn a repo analysis into a starter task list the student can edit."""
    tasks = []
    for path, text in analysis["todos"][:limit]:
        label = text[:55] or "resolve a TODO"
        tasks.append({"title": f"TODO: {label} ({os.path.basename(path)})", "hours": 1})
    for key in analysis["missing"]:
        title, hours = MISSING_TASK[key]
        tasks.append({"title": title, "hours": hours})
    return tasks


def tick_done_tasks(work, analysis):
    """Mark repo-suggested tasks done once the code no longer calls for them (the TODO is gone, the
    README/Makefile/tests now exist). Never unticks, so a task ticked by hand stays ticked."""
    still_open = {t["title"] for t in suggest_tasks(analysis, limit=None)}
    from_repo = {title for title, _hours in MISSING_TASK.values()}
    ticked = 0
    for task in work.get("tasks") or []:
        made_by_repo = task["title"].startswith("TODO: ") or task["title"] in from_repo
        if made_by_repo and not task.get("done") and task["title"] not in still_open:
            task["done"] = True
            ticked += 1
    return ticked


def record_repo(work, repo, analysis):
    """Store the linked repo and its latest state on the project, and tick finished tasks."""
    work["repo"] = repo
    work["repo_state"] = {k: analysis[k] for k in ("remote", "commits", "last_commit", "ahead",
                                                    "behind", "synced_at", "missing")}
    work["repo_state"]["todos"] = len(analysis["todos"])
    return tick_done_tasks(work, analysis)


def repo_status_line(analysis):
    bits = [_("{n} commits").format(n=analysis["commits"])]
    if analysis["last_commit"]:
        bits.append(_("last {when}").format(when=analysis["last_commit"]))
    if analysis["behind"]:
        bits.append(_("{n} behind GitHub").format(n=analysis["behind"]))
    if analysis["ahead"]:
        bits.append(_("{n} to push").format(n=analysis["ahead"]))
    if analysis["todos"]:
        bits.append(_("{n} TODOs").format(n=len(analysis["todos"])))
    if analysis["missing"]:
        bits.append(_("missing: {what}").format(what=", ".join(analysis["missing"])))
    if analysis.get("ticked"):
        bits.append(_("{n} tasks done").format(n=analysis["ticked"]))
    return " · ".join(bits)


def sync_project_repo(data, config, pid, project):
    """Link (auto-detect if needed), fetch from GitHub and analyse one project's repo.
    Returns (analysis, suggestions) or (None, None) if no repo could be found."""
    work = data["work"].setdefault(pid, {"logged": {}})
    repo = work.get("repo") or find_local_repo(project, config)
    checkout = repo_checkout(repo)
    if not checkout or not is_git_repo(checkout):
        return None, None
    analysis = analyze_repo(checkout)
    analysis["ticked"] = record_repo(work, repo, analysis)
    return analysis, suggest_tasks(analysis)


def refresh_all_repos(config):
    """Fetch + analyse every linked (or auto-detectable) project repo. The network/disk work is done
    without the lock; only the results are written under it, so it never blocks the planner for long."""
    data = load_data()
    results = {}
    for project in project_deadlines(data):
        repo = data["work"].get(project["id"], {}).get("repo") or find_local_repo(project, config)
        checkout = repo_checkout(repo)
        if checkout and is_git_repo(checkout):
            results[project["id"]] = (repo, analyze_repo(checkout))
    with changing_data() as fresh:
        for pid, (repo, analysis) in results.items():
            record_repo(fresh["work"].setdefault(pid, {"logged": {}}), repo, analysis)
        fresh["repos_synced_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")


def maybe_refresh_repos(config):
    """Refresh repos during a normal sync, but no more often than repo_sync_minutes."""
    if not config.get("sync_repos"):
        return
    last = load_data().get("repos_synced_at")
    if last and datetime.now() - moment(last) < timedelta(minutes=config.get("repo_sync_minutes", 30)):
        return
    try:
        refresh_all_repos(config)
    except Exception:
        pass  # a repo problem must never break the intra sync


# ---------------------------------------------------------------- notifications

WINDOWS_TOAST = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.BalloonTipTitle = $env:EPIPLAN_TITLE
$n.BalloonTipText = $env:EPIPLAN_BODY
$n.Visible = $true
$n.ShowBalloonTip(10000)
Start-Sleep -Seconds 6
$n.Dispose()
"""


def desktop_notify(config, title, body, urgent=False):
    if IS_WINDOWS:
        env = dict(os.environ, EPIPLAN_TITLE=title, EPIPLAN_BODY=body)
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                          "-Command", WINDOWS_TOAST], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    if IS_MAC:
        env = dict(os.environ, EPIPLAN_TITLE=title, EPIPLAN_BODY=body)
        subprocess.run(["osascript", "-e",
                        'display notification (system attribute "EPIPLAN_BODY") '
                        'with title (system attribute "EPIPLAN_TITLE")'
                        + (" sound name \"Glass\"" if config.get("notify_sound") else "")],
                       env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    command = ["notify-send", "-a", "epiplan", "-i", "appointment-soon", title, body]
    if urgent:
        command[1:1] = ["-u", "critical"]
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if config.get("notify_sound"):
        subprocess.Popen(["canberra-gtk-play", "-i", "alarm-clock-elapsed"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def background_sync(config, state):
    """Sync every few minutes, silently; if Microsoft needs you, say so once a day."""
    last_try = state.get("last_sync_attempt")
    if last_try and datetime.now() - moment(last_try) < timedelta(minutes=BACKGROUND_SYNC_MINUTES):
        return
    state["last_sync_attempt"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        sync(config, allow_window=False)
    except AuthError:
        if state.get("login_warned") != date.today().isoformat():
            state["login_warned"] = date.today().isoformat()
            desktop_notify(config, _("epiplan needs you to log in"), _("Run: epiplan login"))
    except IntraError:
        pass  # offline or intra down: try again later


def hours_until(stamp, now):
    return (moment(stamp) - now).total_seconds() / 3600


def registration_reminders(config, data, now, state):
    """Nudge you to book a slot for defenses, follow-ups, reviews and keynotes while the slot is
    open and you haven't registered: once when it first opens, then at each configured hours-before.
    Returns the set of slot-activity suggestion ids, so the generic reminder can skip them."""
    thresholds = sorted(config.get("register_reminders_hours", []), reverse=True)
    seen = set(state.setdefault("reg_seen", []))
    done = set(state.setdefault("reg_notified", []))
    slots = [s for s in open_suggestions(data)
             if is_slot_activity(config, s.get("kind"), s.get("title", "")) and s["start"] > now.strftime("%Y-%m-%d %H:%M")]

    for s in slots:
        left = hours_until(s["start"], now)
        where = f" @ {s['location']}" if s.get("location") else ""
        if s["id"] not in seen:  # first time the slot is open
            seen.add(s["id"])
            desktop_notify(config, _("Registration open: {title}").format(title=s["title"]),
                           _("book your slot on the intra ({when})").format(when=s["start"][:16]))
            for h in thresholds:  # don't immediately re-fire the milestones already in the past
                if left <= h:
                    done.add(f"{s['id']}@{h}")
        for h in thresholds:
            key = f"{s['id']}@{h}"
            if left <= h and key not in done:
                done.add(key)
                desktop_notify(config, _("Register within ~{h}h: {title}").format(h=h, title=s["title"]),
                               _("{when} - book your slot on the intra now").format(when=s["start"][:16] + where),
                               urgent=True)

    ids = {s["id"] for s in slots}
    state["reg_seen"] = [i for i in seen if i in ids]
    state["reg_notified"] = [k for k in done if k.rsplit("@", 1)[0] in ids]
    return ids


def deadline_reminders(config, data, now, state):
    """Remind you to submit / push before each project deadline."""
    thresholds = sorted(config.get("deadline_reminders_hours", []), reverse=True)
    done = set(state.setdefault("deadline_notified", []))
    deadlines = [view(item) for item in data["items"].values()]
    deadlines = [m for m in deadlines if m.get("kind") == "Project deadline"
                 and not m.get("done") and not m.get("gone") and m["start"] > now.strftime("%Y-%m-%d %H:%M")]

    for m in deadlines:
        left = hours_until(m["start"], now)
        for h in thresholds:
            key = f"{m['id']}@{h}"
            if left <= h and key not in done:
                done.add(key)
                name = m.get("project") or m["title"]
                desktop_notify(config, _("Deadline in ~{h}h: {title}").format(h=h, title=name),
                               _("push / submit on the intra ({when})").format(when=m["start"][:16]), urgent=True)

    ids = {m["id"] for m in deadlines}
    state["deadline_notified"] = [k for k in done if k.rsplit("@", 1)[0] in ids]


def run_notify():
    config = load_config()
    pin_today_start()
    state = load_json(STATE_FILE, {})
    notified = state.setdefault("notified", {})
    background_sync(config, state)

    data = load_data()
    now = datetime.now()
    lead = timedelta(minutes=config["notify_minutes"])
    blocks, _summary = build_plan(data, config)
    upcoming = [view(item) for item in data["items"].values()]
    if config.get("notify_work_blocks"):
        upcoming += [b for b in blocks if not b.get("done")]
    for entry in upcoming:
        if not clock_of(entry["start"]) or entry.get("hidden") or entry.get("done") or entry.get("gone"):
            continue
        if entry.get("kind") == "Project deadline":
            continue
        start = moment(entry["start"])
        key = f"{entry['id']}@{entry['start']}"
        if timedelta(0) <= start - now <= lead and key not in notified:
            notified[key] = now.strftime("%Y-%m-%d %H:%M")
            minutes = max(1, round((start - now).total_seconds() / 60))
            where = f" @ {entry['location']}" if entry.get("location") else ""
            ends = f"-{clock_of(entry['end'])}" if entry.get("end") and clock_of(entry["end"]) else ""
            desktop_notify(config, _("In {n} min: {title}").format(n=minutes, title=entry["title"]),
                           f"{clock_of(entry['start'])}{ends}{where}  {entry.get('module', '')}".strip(), urgent=True)
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    state["notified"] = {k: v for k, v in notified.items() if v > week_ago}

    # Slot activities (defense/review/keynote/follow-up) get their own timed cadence below; project
    # deadlines get push reminders. Both are excluded from the generic "please register" batch.
    slot_ids = registration_reminders(config, data, now, state)
    deadline_reminders(config, data, now, state)

    pending = [s for s in open_suggestions(data, only_important=True) if s["id"] not in slot_ids]
    already_told = set(state.get("suggested", []))
    new = [s for s in pending if s["id"] not in already_told]
    today = date.today().isoformat()
    if new:
        desktop_notify(config, _("Register for {count}").format(count=n_important(len(new))),
                       "\n".join(f"{s['start'][:16]}  {s['title']}" for s in new[:6]))
        state["reminded_on"] = today
    elif pending and now.hour >= config["daily_reminder_hour"] and state.get("reminded_on") != today:
        desktop_notify(config, _("Still not registered: {count}").format(count=n_important(len(pending))),
                       "\n".join(f"{s['start'][:16]}  {s['title']}" for s in pending[:6]))
        state["reminded_on"] = today
    state["suggested"] = [s["id"] for s in pending]
    save_json(STATE_FILE, state)


WINDOWS_TASK = "epiplan-notify"


def set_notifications(on):
    if IS_WINDOWS:
        set_notifications_windows(on)
        return
    if IS_MAC:
        print(_("background reminders are only set up automatically on Linux and Windows for now"))
        return
    service = os.path.join(SYSTEMD_DIR, "epiplan-notify.service")
    timer = os.path.join(SYSTEMD_DIR, "epiplan-notify.timer")
    if not on:
        subprocess.run(["systemctl", "--user", "disable", "--now", "epiplan-notify.timer"], stderr=subprocess.DEVNULL)
        for path in (service, timer):
            if os.path.exists(path):
                os.remove(path)
        subprocess.run(["systemctl", "--user", "daemon-reload"])
        print(_("background reminders are off"))
        return
    os.makedirs(SYSTEMD_DIR, exist_ok=True)
    with open(service, "w") as f:
        f.write("[Unit]\nDescription=epiplan reminders\n\n[Service]\nType=oneshot\n"
                f"ExecStart={sys.executable} {os.path.abspath(__file__)} notify\n")
    with open(timer, "w") as f:
        f.write("[Unit]\nDescription=epiplan reminder check every minute\n\n"
                "[Timer]\nOnCalendar=*-*-* *:*:00\nAccuracySec=5s\n\n[Install]\nWantedBy=timers.target\n")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "epiplan-notify.timer"], check=True)
    desktop_notify(load_config(), _("epiplan reminders are on"),
                   _("You'll be told {n} minutes before your activities.").format(n=load_config()["notify_minutes"]))
    print(_("background reminders are on (checked every minute, even when epiplan is closed)"))


def set_notifications_windows(on):
    if not on:
        subprocess.run(["schtasks", "/Delete", "/TN", WINDOWS_TASK, "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(_("background reminders are off"))
        return
    pyw = sys.executable
    if pyw.lower().endswith("python.exe"):  # pythonw runs with no console window flashing each minute
        pyw = pyw[:-len("python.exe")] + "pythonw.exe"
    run = f'"{pyw}" "{os.path.abspath(__file__)}" notify'
    subprocess.run(["schtasks", "/Create", "/TN", WINDOWS_TASK, "/SC", "MINUTE", "/MO", "1",
                    "/TR", run, "/F"], check=True)
    desktop_notify(load_config(), _("epiplan reminders are on"),
                   _("You'll be told {n} minutes before your activities.").format(n=load_config()["notify_minutes"]))
    print(_("background reminders are on (checked every minute, even when epiplan is closed)"))


# ---------------------------------------------------------------- TUI

HELP_LINES = [
    "Navigation   j/k or arrows: move        n/p or right/left: next/prev week",
    "             t: back to today           enter: details",
    "Editing      a: add entry               e: edit title/time/room",
    "             o: edit notes in $EDITOR   space: done / work block done",
    "             d: delete (yours) / hide (intra) / dismiss (register?)",
    "             H: show hidden             u: undo edits on an intra entry",
    "Work plan    w: edit a project's tasks/hours (in $EDITOR)  T: start a task list",
    "             l: log hours worked today  W: overview  g: suggest tasks from the GitHub repo",
    "Intra        r: activities to register  O: open on intra (to register)",
    "             c: ask the assistant       s: sync now   q: quit",
    "Colours: intra  mine  deadline  work block  register?",
]


def help_text():
    return [_(line) for line in HELP_LINES[:-1]] + ["", _(HELP_LINES[-1])]


class Planner:
    def __init__(self, screen):
        self.screen = screen
        self.config = load_config()
        self.show_hidden = False
        self.register_thread = None      # background auto-sync after you open a page to register
        self.register_watch_id = None
        self.reload()
        self.go_to_today()
        pending = open_suggestions(self.data, only_important=True)
        self.status = _("last sync: {when}   ? for help").format(when=self.data["last_sync"] or _("never"))
        if pending:
            self.status = _("{count} to register for - press r   (? for help)").format(count=n_important(len(pending)))
        curses.curs_set(0)
        curses.use_default_colors()
        for pair, color in enumerate([curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_MAGENTA,
                                      curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_BLUE], start=1):
            curses.init_pair(pair, color, -1)
        self.screen.timeout(30000)  # redraw every 30s to pick up background syncs

    # ---- data

    def reload(self):
        pin_today_start()
        self.data = load_data()
        self.data_mtime = os.path.getmtime(DATA_FILE) if os.path.exists(DATA_FILE) else 0
        self.plan = build_plan(self.data, self.config)

    def change(self, apply):
        with changing_data() as data:
            apply(data)
        self.reload()

    def change_item(self, item_id, apply):
        def on_item(data):
            if item_id in data["items"]:
                apply(data["items"][item_id], data)
        self.change(on_item)

    def go_to_today(self):
        self.week_start = date.today() - timedelta(days=date.today().weekday())
        self.scroll = 0
        rows = self.rows()
        picks = self.selectable(rows)
        self.selected = next(n for n, row in enumerate(picks)
                             if rows[row][0] == "empty" and rows[row][1] == date.today()
                             or rows[row][0] == "item" and day_of(rows[row][1]["start"]) == date.today())

    # ---- layout

    def rows(self):
        """Screen rows for the current week: ('day', date) headers and ('item', merged) entries."""
        week_end = self.week_start + timedelta(days=6)
        items = agenda_between(self.data, self.config, self.week_start, week_end, self.show_hidden, self.plan)
        rows = []
        for offset in range(7):
            day = self.week_start + timedelta(days=offset)
            rows.append(("day", day))
            todays = [m for m in items if day_of(m["start"]) == day]
            rows.extend(("item", m) for m in todays)
            if not todays:
                rows.append(("empty", day))
        return rows

    def selectable(self, rows):
        return [i for i, (kind, _) in enumerate(rows) if kind != "day"]

    def current(self):
        rows = self.rows()
        picks = self.selectable(rows)
        self.selected = max(0, min(self.selected, len(picks) - 1))
        return rows[picks[self.selected]]

    def selected_day(self):
        kind, value = self.current()
        return value if kind == "empty" else day_of(value["start"])

    def item_style(self, merged):
        if merged.get("done") or merged.get("hidden") or merged.get("gone") or merged["source"] == "break":
            return curses.A_DIM
        status = self.plan[1].get(merged["id"])
        if status and status["behind"]:
            return curses.color_pair(5) | curses.A_BOLD
        styles = {"local": curses.color_pair(2), "plan": curses.color_pair(6),
                  "suggest": curses.color_pair(4) | curses.A_BOLD}
        if merged.get("kind") == "Project deadline":
            return curses.color_pair(3) | curses.A_BOLD
        return styles.get(merged["source"], curses.color_pair(1))

    def draw(self):
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        week_end = self.week_start + timedelta(days=6)
        header = " " + _("epiplan  -  week of {a} to {b}").format(
            a=short_date(self.week_start), b=short_date_year(week_end))
        self.screen.addnstr(0, 0, header.ljust(width), width - 1, curses.A_REVERSE | curses.A_BOLD)

        rows = self.rows()
        picks = self.selectable(rows)
        self.selected = max(0, min(self.selected, len(picks) - 1))
        cursor = picks[self.selected]
        body = height - 3
        if cursor < self.scroll:
            self.scroll = max(0, cursor - 1)
        if cursor >= self.scroll + body:
            self.scroll = cursor - body + 1

        work_per_day = defaultdict(float)
        for block in self.plan[0]:
            work_per_day[day_of(block["start"])] += block["hours"]

        for line, (kind, value) in enumerate(rows[self.scroll:self.scroll + body], start=1):
            index = self.scroll + line - 1
            if kind == "day":
                style = curses.A_BOLD | (curses.color_pair(4) if value == date.today() else 0)
                label = long_date(value) + (_(" (today)") if value == date.today() else "")
                if work_per_day[value]:
                    label += _("   - {h} of project work planned").format(h=hours_text(work_per_day[value]))
                self.screen.addnstr(line, 1, label, width - 2, style)
                continue
            if kind == "empty":
                text, style = "    -", curses.A_DIM
            else:
                text, style = "    " + describe(value, self.plan[1]), self.item_style(value)
            if index == cursor:
                style |= curses.A_REVERSE
                text = text.ljust(width - 2)
            self.screen.addnstr(line, 1, text, width - 2, style)

        self.screen.addnstr(height - 2, 1, self.status, width - 2)
        self.screen.refresh()

    # ---- small widgets

    def prompt(self, label, initial=""):
        """One-line editor on the bottom row. Returns the text, or None on Escape."""
        height, width = self.screen.getmaxyx()
        text, pos = list(initial), len(initial)
        curses.curs_set(1)
        self.screen.timeout(-1)
        try:
            while True:
                room = width - len(label) - 2
                start = max(0, pos - room + 1)
                self.screen.move(height - 1, 0)
                self.screen.clrtoeol()
                self.screen.addnstr(height - 1, 0, label, width - 1, curses.A_BOLD)
                self.screen.addnstr(height - 1, len(label), "".join(text[start:start + room]), room)
                self.screen.move(height - 1, len(label) + pos - start)
                key = self.screen.get_wch()
                if key in ("\n", "\r", curses.KEY_ENTER):
                    return "".join(text)
                if key == "\x1b":
                    return None
                if key in (curses.KEY_BACKSPACE, "\x7f", "\b") and pos > 0:
                    pos -= 1
                    del text[pos]
                elif key == curses.KEY_DC and pos < len(text):
                    del text[pos]
                elif key == curses.KEY_LEFT:
                    pos = max(0, pos - 1)
                elif key == curses.KEY_RIGHT:
                    pos = min(len(text), pos + 1)
                elif key in (curses.KEY_HOME, "\x01"):
                    pos = 0
                elif key in (curses.KEY_END, "\x05"):
                    pos = len(text)
                elif key == "\x15":
                    text, pos = [], 0
                elif isinstance(key, str) and key.isprintable():
                    text.insert(pos, key)
                    pos += 1
        finally:
            curses.curs_set(0)
            self.screen.timeout(30000)

    def ask_when(self, label, initial):
        while True:
            answer = self.prompt(label, initial)
            if answer is None or answer.strip() == "":
                return answer
            try:
                return parse_when(answer, self.selected_day())
            except ValueError:
                self.status = _("could not read that date - try 2026-10-02 14:00, tomorrow 9:00, fri, 14:30")
                self.draw()

    def ask_hours(self, label, initial):
        answer = self.prompt(label, f"{initial:g}")
        if answer is None:
            return None
        try:
            return max(0.0, float(answer.replace(",", ".").rstrip("h")))
        except ValueError:
            self.status = _("that is not a number of hours")
            return None

    def popup(self, lines):
        height, width = self.screen.getmaxyx()
        box_w = min(width - 4, max(len(l) for l in lines) + 4)
        box_h = min(height - 2, len(lines) + 2)
        window = curses.newwin(box_h, box_w, (height - box_h) // 2, (width - box_w) // 2)
        window.box()
        for i, line in enumerate(lines[:box_h - 2], start=1):
            window.addnstr(i, 2, line, box_w - 4)
        window.refresh()
        window.getch()

    def edit_in_editor(self, text):
        with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as f:
            f.write(text)
            path = f.name
        curses.endwin()
        try:
            subprocess.call([default_editor(), path])
            with open(path) as f:
                return f.read().rstrip("\n")
        finally:
            os.unlink(path)
            self.screen.refresh()

    # ---- actions on entries

    def add(self):
        title = self.prompt(_("Title: "))
        if not title:
            return
        default_start = self.selected_day().isoformat() + " 09:00"
        start = self.ask_when(_("Start (e.g. 14:00, fri 10:00, 2026-10-02): "), default_start)
        if not start:
            return
        end = self.ask_when(_("End (empty = none): "), "") or ""
        location = self.prompt(_("Where (optional): ")) or ""
        key, item = new_local_item(title, start, end, location)
        self.change(lambda data: data["items"].__setitem__(key, item))
        self.week_start = day_of(start) - timedelta(days=day_of(start).weekday())
        self.status = _("added '{title}'   (w on it turns it into a project with planned work time)").format(title=title)

    def edit(self, merged):
        changes = {}
        for field, label in [("title", _("Title: ")), ("start", _("Start: ")), ("end", _("End: ")), ("location", _("Where: "))]:
            if field in ("start", "end"):
                value = self.ask_when(label, merged.get(field, ""))
            else:
                value = self.prompt(label, merged.get(field, ""))
            if value is None:
                break
            if value != merged.get(field, "") and not (field == "start" and not value):
                changes[field] = value

        def apply(item, data):
            for field, value in changes.items():
                edit_item(item, field, value)
        self.change_item(merged["id"], apply)
        self.status = _("saved") + (_(" (your changes survive syncs; u to undo them)") if merged["source"] == "intra" else "")

    def edit_notes(self, merged):
        notes = self.edit_in_editor(merged.get("notes", ""))
        self.change_item(merged["id"], lambda item, data: edit_item(item, "notes", notes))
        self.status = _("notes saved")

    def toggle_done(self, merged):
        if merged["source"] == "plan":
            self.toggle_block(merged)
        else:
            self.change_item(merged["id"], lambda item, data: edit_item(item, "done", not merged.get("done")))

    def delete(self, merged):
        if merged["source"] == "suggest":
            self.change(lambda data: data["dismissed"].append(merged["id"]))
            self.status = _("dismissed - it won't remind you about this one again")
        elif merged["source"] == "plan":
            self.status = _("work blocks are planned for you: change the hours left with w, or log time with l")
        elif merged["source"] == "local":
            if self.prompt(_("Delete '{title}'? (y/N) ").format(title=merged["title"])) in ("y", "Y", "o", "O"):
                self.change(lambda data: data["items"].pop(merged["id"], None))
                self.status = _("deleted")
        else:
            self.change_item(merged["id"], lambda item, data: edit_item(item, "hidden", not merged.get("hidden")))
            self.status = _("hidden (H shows hidden entries)") if not merged.get("hidden") else _("unhidden")

    def reset(self, merged):
        if merged["source"] == "intra" and self.prompt(_("Drop your edits/notes on this entry? (y/N) ")) in ("y", "Y", "o", "O"):
            self.change_item(merged["id"], lambda item, data: item.__setitem__("overrides", {}))
            self.status = _("back to the intra version")

    def details(self, merged):
        lines = [merged["title"], ""]
        for label, field in [("Module", "module"), ("Type", "kind"), ("Start", "start"), ("End", "end"),
                             ("Where", "location"), ("Link", "url")]:
            if merged.get(field):
                lines.append(f"{_(label):<7} {merged[field]}")
        source = {"intra": "intra.epitech.eu", "local": "your entry", "plan": "planned work time",
                  "break": "recurring break (edit with: epiplan config)",
                  "suggest": "not registered yet - O opens the intra page to register"}[merged["source"]]
        lines.append(f"{_('Source'):<7} {_(source)}")
        if merged.get("overrides"):
            lines.append(f"{_('Edited'):<7} {', '.join(merged['overrides'])}")
        pid = merged.get("project") or merged["id"]
        status = self.plan[1].get(pid)
        if status:
            lines += ["", self.project_line(status)]
        repo_state = self.data["work"].get(pid, {}).get("repo_state")
        if repo_state:
            lines.append(_("Repo: {status}").format(status=repo_status_line(dict(repo_state, todos=[None] * repo_state.get("todos", 0)))))
        if merged.get("notes"):
            lines += ["", _("Notes:")] + merged["notes"].splitlines()
        self.popup(lines + ["", _("(any key)")])

    # ---- work plan actions

    def project_of(self, merged):
        """The project or prep task an entry stands for: a planned block's target, a review/
        keynote/defense's prep, or the entry itself."""
        if merged["source"] == "plan":
            return merged["project"]
        if merged["source"] in ("intra", "local"):
            if prep_category(self.config, merged.get("kind"), merged["title"]):
                return "prep:" + merged["id"]
            return merged["id"]
        return None

    def project_line(self, status):
        guess = _(" (guessed - press w to set it)") if status["guessed"] else ""
        line = _("{title}: {left} left of {estimate}{guess}, due {deadline}").format(
            title=status["title"], left=hours_text(status["left"]),
            estimate=hours_text(status["estimate"]), guess=guess, deadline=status["deadline"])
        if status["behind"]:
            line += _("  - NOT ENOUGH FREE TIME, {behind} short").format(behind=hours_text(status["behind"]))
        return line

    @property
    def TASK_HELP(self):
        return [
            _("# One task per line, as:  HOURS  <what you'll do>   e.g.   3  build the model notebook"),
            _("# Order = the order they'll be scheduled. Delete every line to plan by a single number instead."),
            _("# Put an x in front of a finished task (x 3  ...); syncing the repo ticks suggested ones itself."),
            "",
        ]

    def set_hours_left(self, merged):
        project_id = self.project_of(merged)
        if not project_id:
            return
        work = self.data["work"].get(project_id, {})
        if work.get("tasks"):
            self.edit_tasks(project_id, work["tasks"])
        else:
            self.edit_single_estimate(project_id)

    def edit_tasks(self, project_id, tasks):
        lines = self.TASK_HELP + [("x " if t.get("done") else "") + f"{t['hours']:g}\t{t['title']}" for t in tasks]
        edited = self.parse_tasks(self.edit_in_editor("\n".join(lines)))
        if edited is None:
            return

        def apply(data):
            work = data["work"].setdefault(project_id, {"logged": {}})
            if edited:
                work["tasks"] = edited
                work.pop("estimate", None)
            else:  # all lines deleted: fall back to a flat estimate
                work.pop("tasks", None)
                work["estimate"] = sum(t["hours"] for t in tasks)
        self.change(apply)
        self.status = _("{n} tasks, {total} total").format(n=len(edited), total=hours_text(sum(t["hours"] for t in edited))) \
            if edited else _("task list cleared - press w again to set a single number of hours")

    def edit_single_estimate(self, project_id):
        status = self.plan[1].get(project_id)
        answer = self.prompt(_("Hours left (0 = drop), or type a task list with T: "), f"{status['left']:g}" if status else "10")
        if answer is None:
            return
        try:
            left = max(0.0, float(answer.replace(",", ".").rstrip("h")))
        except ValueError:
            self.status = _("that is not a number of hours")
            return

        def apply(data):
            work = data["work"].setdefault(project_id, {"logged": {}})
            if left == 0 and not project_id.startswith("intra:proj:") and not project_id.startswith("prep:"):
                data["work"].pop(project_id, None)
            else:
                work["estimate"] = sum(work.get("logged", {}).values()) + left
        self.change(apply)
        self.status = _("planned {h}; press w again to break it into named tasks").format(h=hours_text(left))

    def repo_suggest(self, merged):
        """Link + sync the project's GitHub repo, then open the task list seeded with suggestions."""
        pid = self.project_of(merged)
        if not pid or pid.startswith("prep:") or pid not in self.plan[1]:
            self.status = _("select a project deadline or work block first")
            return
        name = self.plan[1][pid]["title"]
        project = {"id": pid, "project": name, "title": name}
        self.status = _("syncing {name} with GitHub...").format(name=name)
        self.draw()
        analysis = suggestions = None

        def apply(data):
            nonlocal analysis, suggestions
            analysis, suggestions = sync_project_repo(data, config=self.config, pid=pid, project=project)
        self.change(apply)
        if not analysis:
            self.status = _("no GitHub repo found for {name} - link it with: epiplan repo \"{name}\" <path-or-owner/repo>").format(name=name)
            return
        existing = self.data["work"].get(pid, {}).get("tasks") or []
        seen = {t["title"] for t in existing}
        seeded = list(existing) + [t for t in suggestions if t["title"] not in seen]
        self.status = _("{name}: {status}").format(name=name, status=repo_status_line(analysis))
        self.edit_tasks(pid, seeded or [{"title": "", "hours": 2}])

    def new_task_list(self, merged):
        """Start a task breakdown for a project that has none yet."""
        project_id = self.project_of(merged)
        status = self.plan[1].get(project_id) if project_id else None
        if not project_id or not status:
            self.status = _("select a project deadline or a work block first")
            return
        seed = [{"title": "", "hours": status["left"]}] if status["left"] else [{"title": "", "hours": 2}]
        self.edit_tasks(project_id, self.data["work"].get(project_id, {}).get("tasks") or seed)

    @staticmethod
    def parse_tasks(text):
        if text is None:
            return None
        tasks = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            done = bool(re.match(r"x\s", line, re.I))
            if done:
                line = line[1:].strip()
            hours, _, title = line.partition("\t")
            if not title:
                hours, _, title = line.partition(" ")
            try:
                hours = float(hours.replace(",", ".").rstrip("h"))
            except ValueError:
                continue
            if title.strip() and hours > 0:
                tasks.append({"title": title.strip(), "hours": hours, **({"done": True} if done else {})})
        return tasks

    def log_hours(self, merged):
        project_id = self.project_of(merged)
        status = self.plan[1].get(project_id) if project_id else None
        if not status:
            self.status = _("pick a project deadline or a work block (w makes any entry a project)")
            return
        hours = self.ask_hours(_("Hours worked on {title} today: ").format(title=status["title"]), status["done_today"])
        if hours is not None:
            self.change(lambda data: data["work"].setdefault(project_id, {"logged": {}})["logged"]
                        .__setitem__(date.today().isoformat(), hours))
            self.status = _("logged {h} today").format(h=hours_text(hours))

    def toggle_block(self, block):
        if day_of(block["start"]) != date.today():
            self.status = _("only today's blocks can be ticked; for other work use l to log hours")
            return
        change = -block["hours"] if block.get("done") else block["hours"]

        def apply(data):
            logged = data["work"].setdefault(block["project"], {"logged": {}})["logged"]
            today = date.today().isoformat()
            logged[today] = max(0, logged.get(today, 0) + change)
        self.change(apply)

    def overview(self):
        summary = sorted(self.plan[1].items(), key=lambda pair: pair[1]["deadline"])
        if not summary:
            self.popup([_("No projects yet."), _("Intra project deadlines appear here after a sync;"),
                        _("press w on any entry to plan work time for it."), "", _("(any key)")])
            return
        today = date.today()
        lines = [_("Project work - {hours}, max {cap}/day").format(
            hours=self.config["work_hours"], cap=hours_text(self.config["max_work_hours_per_day"])), ""]
        for project_id, status in summary:
            planned_today = sum(b["hours"] for b in self.plan[0]
                                if b["project"] == project_id and day_of(b["start"]) == today)
            lines.append(self.project_line(status))
            lines.append(_("    today: {planned} planned, {done} done").format(
                planned=hours_text(planned_today), done=hours_text(status["done_today"])))
        self.popup(lines + ["", _("(any key)")])

    def registrations(self):
        suggestions = open_suggestions(self.data)
        if not suggestions:
            self.popup([_("Nothing to register for right now."), "", _("(any key)")])
            return
        lines = [_("Activities in your modules you are not registered for"),
                 _("(! = important; they also show in your week)"), ""]
        for s in suggestions:
            mark = "!" if s.get("important") else " "
            full = _(" (full)") if s.get("full") else ""
            lines.append(f"{mark} {s['start'][:16]:<16}  {s['title']}  ({s.get('module', '')}){full}")
        lines += ["", _("Select one in the week view and press O to register on the intra, d to dismiss."), _("(any key)")]
        self.popup(lines)

    def wrap_lines(self, text, width=76):
        out = []
        for line in text.split("\n"):
            while len(line) > width:
                cut = line.rfind(" ", 0, width)
                if cut <= 0:
                    cut = width
                out.append(line[:cut])
                line = "    " + line[cut:].lstrip()
            out.append(line)
        return out

    def chat(self):
        question = self.prompt(_("Ask about the intra: "))
        if not question:
            return
        answer = assistant_answer(self.data, self.config, question)
        self.popup(self.wrap_lines(answer) + ["", _("(any key)")])

    def watch_registration(self, suggestion_id):
        """After opening the intra to register, re-sync in the background until the registration
        shows up, so the schedule updates on its own without pressing 's'."""
        self.status = _("opened the intra - I'll refresh your schedule once you've registered")
        self.register_watch_id = suggestion_id
        self.screen.timeout(4000)  # poll faster so the auto-refresh appears promptly

        def poll():
            for _try in range(8):  # ~2 minutes of chances to catch the registration
                time.sleep(15)
                try:
                    sync(self.config, allow_window=False)
                except IntraError:
                    continue
                if suggestion_id not in load_data().get("suggestions", {}):
                    return  # registered - the main loop will notice and stop polling

        self.register_thread = threading.Thread(target=poll, daemon=True)
        self.register_thread.start()

    def check_register_watch(self):
        """Called on each idle tick while a post-register sync is running."""
        if self.register_thread is None:
            return
        registered = self.register_watch_id not in self.data.get("suggestions", {})
        if registered or not self.register_thread.is_alive():
            if registered:
                self.status = _("schedule updated - you're now registered")
            self.register_thread = None
            self.register_watch_id = None
            self.screen.timeout(30000)

    def do_sync(self):
        self.status = _("syncing with intra (a Chrome window opens if Microsoft needs you)...")
        self.draw()
        try:
            count = sync(self.config)
            self.reload()
            pending = len(open_suggestions(self.data, only_important=True))
            self.status = _("synced {count} intra entries at {when}").format(count=count, when=self.data["last_sync"])
            if pending:
                self.status += _(" - {count} important to register for (r)").format(count=pending)
        except IntraError as err:
            self.status = str(err)

    # ---- main loop

    def run(self):
        while True:
            self.draw()
            try:
                key = self.screen.get_wch()
            except curses.error:  # timeout: refresh if a sync (background or post-register) changed things
                if os.path.exists(DATA_FILE) and os.path.getmtime(DATA_FILE) != self.data_mtime:
                    self.reload()
                self.check_register_watch()
                continue
            kind, value = self.current()
            merged = value if kind == "item" else None
            owned = merged and merged["source"] in ("intra", "local")
            is_break = merged and merged["source"] == "break"
            if key in ("q", "Q"):
                return
            elif key in ("j", curses.KEY_DOWN):
                self.selected += 1
            elif key in ("k", curses.KEY_UP):
                self.selected = max(0, self.selected - 1)
            elif key in ("n", curses.KEY_RIGHT, curses.KEY_NPAGE):
                self.week_start += timedelta(days=7)
                self.selected = self.scroll = 0
            elif key in ("p", curses.KEY_LEFT, curses.KEY_PPAGE):
                self.week_start -= timedelta(days=7)
                self.selected = self.scroll = 0
            elif key == "t":
                self.go_to_today()
            elif key == "a":
                self.add()
            elif key == "s":
                self.do_sync()
            elif key == "H":
                self.show_hidden = not self.show_hidden
            elif key == "r":
                self.registrations()
            elif key == "W":
                self.overview()
            elif key == "c":
                self.chat()
            elif key == "?":
                self.popup(help_text() + ["", _("(any key)")])
            elif is_break and key in (" ", "d", "e", "o", "w", "l", "T", "u", "\n", "\r",
                                      curses.KEY_ENTER, curses.KEY_DC):
                self.status = _("breaks are set with 'epiplan config' (edit the breaks list there)")
            elif owned and key == "e":
                self.edit(merged)
            elif owned and key == "o":
                self.edit_notes(merged)
            elif merged and key == " " and merged["source"] != "suggest":
                self.toggle_done(merged)
            elif merged and key in ("d", curses.KEY_DC):
                self.delete(merged)
            elif owned and key == "u":
                self.reset(merged)
            elif merged and key == "w":
                self.set_hours_left(merged)
            elif merged and key == "T":
                self.new_task_list(merged)
            elif merged and key == "g":
                self.repo_suggest(merged)
            elif merged and key == "l":
                self.log_hours(merged)
            elif merged and key in ("\n", "\r", curses.KEY_ENTER):
                self.details(merged)
            elif merged and key == "O" and merged.get("url"):
                open_in_browser(merged["url"])
                if merged["source"] == "suggest":
                    self.watch_registration(merged["id"])


def run_tui(screen):
    Planner(screen).run()


# ---------------------------------------------------------------- CLI

USAGE_LINES = [
    ("", "epiplan - a terminal planner synced with intra.epitech.eu"),
    ("epiplan", "open the planner"),
    ("epiplan login", "log in once in a Chrome window (--paste: paste the cookie instead)"),
    ("epiplan sync", "pull activities from the intra"),
    ("epiplan list [DAYS]", "print the agenda, work plan and reminders (default 7 days)"),
    ("epiplan add TITLE WHEN [END]", 'add a personal entry, e.g. epiplan add "Gym" "tomorrow 18:00" 19:30'),
    ("epiplan ask \"question\"", "ask the intra assistant one question"),
    ("epiplan chat", "chat with the intra assistant"),
    ("epiplan repos", "sync each project's GitHub repo and suggest tasks"),
    ("epiplan repo \"name\" PATH|owner/repo", "link a project to its GitHub repo"),
    ("epiplan notifications on|off|test", "background reminders (before activities, registrations)"),
    ("epiplan lang en|fr", "choose the language (English / French)"),
    ("epiplan config", "edit settings (work hours, breaks, language, keywords...)"),
]

FR_USAGE = {
    "epiplan - a terminal planner synced with intra.epitech.eu":
        "epiplan - un agenda de terminal synchronisé avec intra.epitech.eu",
    "open the planner": "ouvrir l'agenda",
    "log in once in a Chrome window (--paste: paste the cookie instead)":
        "se connecter une fois dans une fenêtre Chrome (--paste : coller le cookie)",
    "pull activities from the intra": "récupérer les activités de l'intra",
    "print the agenda, work plan and reminders (default 7 days)":
        "afficher l'agenda, le plan de travail et les rappels (7 jours par défaut)",
    'add a personal entry, e.g. epiplan add "Gym" "tomorrow 18:00" 19:30':
        'ajouter une entrée perso, ex. epiplan add "Sport" "tomorrow 18:00" 19:30',
    "ask the intra assistant one question": "poser une question à l'assistant intra",
    "chat with the intra assistant": "discuter avec l'assistant intra",
    "sync each project's GitHub repo and suggest tasks":
        "synchroniser le dépôt GitHub de chaque projet et suggérer des tâches",
    "link a project to its GitHub repo": "lier un projet à son dépôt GitHub",
    "background reminders (before activities, registrations)":
        "rappels en arrière-plan (avant les activités, inscriptions)",
    "choose the language (English / French)": "choisir la langue (anglais / français)",
    "edit settings (work hours, breaks, language, keywords...)":
        "modifier les réglages (heures de travail, pauses, langue, mots-clés...)",
}


def usage():
    label = "Usage:" if LANG == "en" else "Utilisation :"
    lines = [FR_USAGE.get(USAGE_LINES[0][1], USAGE_LINES[0][1]) if LANG == "fr" else USAGE_LINES[0][1], "", label]
    for command, desc in USAGE_LINES[1:]:
        desc = FR_USAGE.get(desc, desc) if LANG == "fr" else desc
        lines.append(f"  {command:<34} {desc}")
    print("\n".join(lines))


def project_deadlines(data):
    for item in data["items"].values():
        merged = view(item)
        if merged.get("kind") == "Project deadline" and not merged.get("gone"):
            yield merged


def cmd_repos():
    """Link, fetch from GitHub and analyse every project's repo; print a status line each."""
    config = load_config()
    with changing_data() as data:
        projects = list(project_deadlines(data))
        if not projects:
            print("no project deadlines yet - run: epiplan sync")
            return
        for project in projects:
            name = project_name_of(project)
            print(_("syncing {name} with GitHub...").format(name=name))
            analysis, suggestions = sync_project_repo(data, config, project["id"], project)
            if not analysis:
                print("  " + _("no GitHub repo found for {name} - link it with: epiplan repo \"{name}\" <path-or-owner/repo>").format(name=name))
                continue
            print("  " + repo_status_line(analysis))
            if suggestions:
                print("  " + "suggested tasks: " + "; ".join(t["title"] for t in suggestions[:5]))


def cmd_repo(query, repo):
    """Manually link the project matching QUERY to a repo path or owner/name slug."""
    config = load_config()
    with changing_data() as data:
        match = next((p for p in project_deadlines(data) if query.lower() in project_name_of(p).lower()), None)
        if not match:
            sys.exit(_("no project matches \"{query}\"").format(query=query))
        stored = os.path.abspath(repo) if os.path.isdir(repo) else repo
        data["work"].setdefault(match["id"], {"logged": {}})["repo"] = stored
    print(_("linked {name} to {repo}").format(name=project_name_of(match), repo=stored))


def run_chat():
    """Interactive intra assistant in the terminal."""
    config = load_config()
    print("\n".join(_(line) for line in ASSISTANT_HELP_LINES))
    print()
    while True:
        try:
            question = input(_("Ask about the intra: "))
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if question.strip().lower() in ("quit", "exit", "q", "bye", "quitter"):
            return
        if question.strip():
            print(assistant_answer(load_data(), config, question) + "\n")


def login(paste=False):
    config = load_config()
    if paste:
        print(_("Copy the 'user' cookie of intra.epitech.eu from your browser's dev tools."))
        config["token"] = getpass.getpass(_("user cookie (input hidden): ")).strip()
        if not config["token"]:
            sys.exit(_("nothing entered"))
    else:
        if not can_open_window():
            sys.exit(_("no graphical display - use: epiplan login --paste"))
        print(_("A Chrome window is opening: log in with your Epitech Microsoft account"))
        print(_("and tick 'Stay signed in' so future logins happen on their own."))
        config["token"] = browser_login(visible=True)
        if not config["token"]:
            sys.exit(_("login was not completed"))
    save_config(config)
    try:
        print(_("Logged in - synced {count} intra entries.").format(count=sync(config, allow_window=False)))
    except IntraError as err:
        print(_("Logged in, but the first sync failed: {err}").format(err=err))


def main(args):
    command = args[0] if args else "tui"
    set_language(load_config())
    if command == "tui":
        if curses is None:
            sys.exit("the planner UI needs curses. On Windows: pip install windows-curses")
        os.environ.setdefault("ESCDELAY", "25")
        data, config = load_data(), load_config()
        stale = not data["last_sync"] or data["last_sync"] < (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        if stale and (config.get("token") or os.environ.get("EPITECH_TOKEN") or os.path.isdir(PROFILE_DIR)):
            print(_("syncing with intra (logging in again if needed)..."))
            try:
                sync(config)
            except IntraError as err:
                print(err)
                time.sleep(1.5)
        curses.wrapper(run_tui)
    elif command == "login":
        login(paste="--paste" in args)
    elif command == "sync":
        try:
            print(_("synced {count} intra entries").format(count=sync(load_config())))
        except IntraError as err:
            sys.exit(str(err))
    elif command == "list":
        pin_today_start()
        print_agenda(load_data(), load_config(), int(args[1]) if len(args) > 1 else 7)
    elif command == "add" and len(args) >= 3:
        start = parse_when(args[2])
        end = parse_when(args[3], day_of(start)) if len(args) > 3 else ""
        key, item = new_local_item(args[1], start, end)
        with changing_data() as data:
            data["items"][key] = item
        print(_("added '{title}' on {start}").format(title=args[1], start=start))
    elif command == "ask":
        print(assistant_answer(load_data(), load_config(), " ".join(args[1:])))
    elif command == "chat":
        run_chat()
    elif command == "repos":
        cmd_repos()
    elif command == "repo" and len(args) >= 3:
        cmd_repo(args[1], args[2])
    elif command == "notify":
        run_notify()
    elif command == "notifications" and len(args) > 1 and args[1] in ("on", "off"):
        set_notifications(args[1] == "on")
    elif command == "notifications" and len(args) > 1 and args[1] == "test":
        desktop_notify(load_config(), _("In 10 min: Test activity"),
                       _("09:00-12:00 @ Amphi-1  (this is a test)"), urgent=True)
    elif command == "lang" and len(args) > 1 and args[1][:2].lower() in ("en", "fr"):
        config = load_config()
        config["lang"] = args[1][:2].lower()
        save_config(config)
        set_language(config)
        print(_("language set to {lang}").format(lang="français" if LANG == "fr" else "English"))
    elif command == "config":
        config = load_config()
        save_config(config)  # write every setting out so they can all be edited
        subprocess.call([default_editor(), CONFIG_FILE])
    else:
        usage()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        sys.exit(130)  # Ctrl+C quits quietly; every change is already saved
