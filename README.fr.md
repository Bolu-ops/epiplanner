# epiplan

Un agenda qui tourne dans ton terminal et reste synchronisé avec **intra.epitech.eu**. Il affiche
tes activités inscrites et les rendus de projet, planifie le temps à passer sur chaque projet
(découpé en tâches concrètes), te rappelle de t'inscrire aux activités importantes, te prépare aux
reviews, keynotes et soutenances, et peut afficher une notification 10 minutes avant chaque début.

Tout est modifiable, et il fonctionne en **français ou en anglais**.

## Installation

**Linux / macOS :**

```
bash install.sh
```

**Windows** (dans PowerShell, depuis ce dossier) :

```
powershell -ExecutionPolicy Bypass -File install.ps1
```

L'installateur met tout en place sur ton ordinateur, te demande de choisir une langue et
(facultatif) une pause quotidienne à garder libre, puis affiche où se trouve ce guide. Ouvre ensuite
un **nouveau terminal** pour que la commande `epiplan` soit trouvée. Sous Linux/macOS, l'installateur
ajoute lui-même `~/.local/bin` à ton `PATH` (dans `~/.bashrc`, `~/.zshrc` ou la config de fish) et
propose de te connecter tout de suite.

**`epiplan: command not found` ?** Tu es encore dans le terminal de l'installation : ouvre-en un
nouveau, ou lance `exec $SHELL`. `~/.local/bin/epiplan login` marche aussi depuis n'importe où.

## Première utilisation

```
epiplan login      # ouvre Chrome une fois ; connecte-toi avec ton compte Microsoft Epitech
epiplan            # ouvre l'agenda
```

Coche **« Rester connecté »** lors de cette première connexion pour qu'epiplan puisse renouveler son
accès tout seul ensuite. Pas de navigateur graphique (ex. en SSH) ? Utilise `epiplan login --paste`
et colle le cookie `user` de intra.epitech.eu. Ton mot de passe n'est jamais stocké — seulement la
session de l'intra.

## Touches dans l'agenda

| Touche | Action |
|--------|--------|
| `j`/`k`, flèches | monter / descendre |
| `n`/`p`, `t` | semaine suivante / précédente, revenir à aujourd'hui |
| `Entrée` | détails |
| `a` | ajouter une entrée perso |
| `e` | modifier titre / heure / salle |
| `o` | modifier les notes dans ton `$EDITOR` |
| `espace` | marquer fait (coche aussi un bloc de travail) |
| `d` | supprimer (perso) / masquer (intra) / ignorer (une suggestion d'inscription) |
| `H` | afficher les entrées masquées |
| `u` | annuler tes modifs sur une entrée intra |
| `w` | modifier les tâches et heures d'un projet (ouvre ton `$EDITOR`) |
| `T` | créer une liste de tâches pour un projet |
| `g` | synchroniser le dépôt GitHub du projet et suggérer des tâches depuis le code |
| `l` | noter les heures travaillées aujourd'hui |
| `W` | vue d'ensemble des projets |
| `r` | activités auxquelles tu pourrais t'inscrire |
| `O` | ouvrir l'élément sélectionné sur l'intra (pour s'inscrire) |
| `c` | poser une question à l'assistant intra |
| `s` | synchroniser maintenant |
| `?` | aide |
| `q` | quitter |

## Commandes

| Commande | Rôle |
|----------|------|
| `epiplan` | ouvrir l'agenda |
| `epiplan sync` | récupérer les activités de l'intra |
| `epiplan list [JOURS]` | afficher l'agenda + le plan de travail (7 jours par défaut) |
| `epiplan add "Titre" "quand" [fin]` | ajouter une entrée, ex. `epiplan add "Sport" "tomorrow 18:00" 19:30` |
| `epiplan ask "question"` | poser une question à l'assistant intra |
| `epiplan chat` | discuter avec l'assistant intra |
| `epiplan repos` | lier + synchroniser le dépôt GitHub de chaque projet et suggérer des tâches |
| `epiplan repo "nom" CHEMIN\|owner/repo` | lier un projet à son dépôt (chemin ou slug GitHub) |
| `epiplan notifications on\|off\|test` | rappels en arrière-plan (Linux) |
| `epiplan lang en\|fr` | changer la langue |
| `epiplan config` | modifier tous les réglages dans ton `$EDITOR` |

## Le plan de travail

epiplan répartit les heures restantes de chaque projet sur les jours avant son rendu, du rendu le
plus proche au plus lointain, dans ton temps libre. Appuie sur `w` sur un projet (ou un de ses blocs
de travail) pour modifier la liste des tâches — une ligne `HEURES  ce que tu feras` — afin que chaque
bloc indique exactement quoi faire. Il ajoute aussi du temps de préparation avant chaque review,
keynote et soutenance à laquelle tu es inscrit.

## Dépôts GitHub &amp; suggestions de tâches

epiplan peut lier chaque projet à son dépôt GitHub, se synchroniser avec lui et suggérer des tâches à
partir du code :

- **`epiplan repos`** lie chaque projet (en détectant un clone local dont le nom de dossier
  correspond, ou un slug que tu as réglé), lance `git fetch` pour refléter GitHub, et affiche l'état
  de chaque dépôt et des tâches suggérées. Dans l'agenda, appuie sur **`g`** sur un projet pour le
  faire pour un seul et ouvrir sa liste de tâches pré-remplie avec les suggestions.
- **Lier :** si la détection échoue, lie manuellement avec `epiplan repo "Corewar" ~/chemin/du/clone`
  ou `epiplan repo "Corewar" EpitechPGE1-2025/G-CPE-200-...`. Un slug est cloné dans
  `~/.local/share/epiplan/repos/` puis mis à jour aux synchros suivantes.
- **Ce qu'il suggère :** les `TODO`/`FIXME` de ton code deviennent des tâches, et il signale les
  livrables courants manquants (README, Makefile pour les projets C, tests). Il montre aussi les
  commits, la date du dernier, et l'écart avec GitHub (visible dans les détails d'un projet).
- **Tâches cochées :** chaque synchro du dépôt coche les tâches suggérées que le code montre finies —
  un `TODO` disparu, ou un README/Makefile/des tests désormais présents. Les tâches cochées sortent
  du planning et comptent comme faites. Dans l'éditeur de tâches (`w`) elles s'affichent `x 1  ...` ;
  mets un `x` devant n'importe quelle tâche pour la cocher toi-même. La synchro ne décoche jamais.
- **Automatique :** une fois un projet lié, son dépôt se rafraîchit aussi lors de la synchro normale
  — au plus toutes les 30 minutes (`repo_sync_minutes`), pour rester en phase avec ton agenda sans
  rien lancer. Mets `sync_repos` à `false` pour le garder uniquement à la demande. `g` et
  `epiplan repos` rafraîchissent toujours immédiatement.
- **Accès :** pour les dépôts privés Epitech il utilise ton clone existant, ou l'auth `gh`/SSH pour
  cloner un slug (lance `gh auth login` une fois, ou configure ta clé SSH). Git tourne de façon non
  interactive, donc il ne bloque jamais.

## L'assistant

Appuie sur `c` dans l'agenda, ou lance `epiplan chat` (ou `epiplan ask "…"`), pour poser des
questions sur ton intra en langage naturel :

- « qu'est-ce que j'ai aujourd'hui / demain / cette semaine ? »
- « c'est quand ma prochaine soutenance / review / keynote / examen ? »
- « sur quoi je dois travailler maintenant ? » · « je suis en retard ? »
- « combien d'heures reste-t-il sur Corewar ? »
- « à quoi dois-je m'inscrire ? »
- « c'est quand le rendu de Tardis ? »
- « combien de crédits ai-je ? » · « quel est mon GPA ? » · « mes notes » · « mon netsoul ? »

Chaque synchro récupère aussi ton **profil** intra (crédits, GPA, notes, netsoul/temps de log, et le
reste), pour que l'assistant puisse y répondre. Il répond **uniquement** à partir de tes données intra
synchronisées — il tourne entièrement hors
ligne, ne nécessite aucun compte ni clé API, et n'envoie rien nulle part. Par conception il ne parle
que de ton intra et **n'écrit pas de code** et ne répond pas hors sujet.

## Réglages (`epiplan config`)

- `work_hours`, `max_work_hours_per_day`, `max_block_hours` — quand et combien tu travailles.
- `breaks` — plages occupées récurrentes, ex. le déjeuner. Une pause avec un `label` s'affiche dans
  ta semaine ; sans `label` elle est juste gardée libre. `if_activity` limite une pause aux jours
  contenant ce mot.
- `notify_minutes`, `notify_sound` — les rappels.
- `important`, `prep_hours`, `prep_lead_days` — quelles activités comptent et combien de temps de prépa.
- `register_activities` — quelles activités demandent un créneau (repérées par un mot du titre/type) :
  soutenances, follow-ups, reviews, keynotes, bootstraps, kick-offs. Ajoute tes propres mots ici.
- `register_reminders_hours` — heures avant une telle activité où te rappeler de réserver, ex. `[72, 48, 25]`.
- `deadline_reminders_hours` — heures avant un rendu de projet où te rappeler de pousser/rendre, ex. `[24, 2]`.
- `repo_search_dirs` — dossiers où chercher le clone local d'un projet pour lier les dépôts.
- `sync_repos`, `repo_sync_minutes` — rafraîchir les dépôts GitHub liés lors de la synchro, au plus toutes les N minutes (30 par défaut).
- `lang` — `en`, `fr`, ou vide pour la détection automatique.

## Notifications

`epiplan notifications on` installe une tâche en arrière-plan qui vérifie chaque minute — même
l'agenda fermé — et te rappelle :

- **tout ce qui commence dans 10 minutes** (activités, tes entrées, blocs de travail planifiés) ;
- **la réservation des activités à créneau** — soutenances, follow-ups, reviews, keynotes, bootstraps
  et kick-offs demandent de réserver un créneau. Tant que le créneau est ouvert et que tu n'es pas
  inscrit, tu es prévenu à son ouverture, puis à 72h, 48h et 25h avant (le rappel à 25h arrive juste
  avant la fenêtre habituelle de 24h), pour ne pas oublier de choisir un créneau ;
- **les rendus de projet** — un rappel de pousser/rendre 24h et 2h avant chaque rendu ;
- **les activités importantes** auxquelles tu n'es pas encore inscrit (un rappel quotidien).

Elle utilise **systemd** sous Linux et le **Planificateur de tâches** sous Windows. Sur macOS l'agenda
et les notifications quand il est ouvert fonctionnent, mais la vérification automatique chaque minute
n'est pas encore mise en place.

## Comment il fonctionne sous Windows

C'est le même programme que sous Linux/macOS — Windows le met juste en place un peu différemment :

- **Installation :** `install.ps1` copie l'application dans `%LOCALAPPDATA%\epiplan-app\`, y crée un
  environnement virtuel et installe `windows-curses` (pour l'interface) et Playwright (pour la
  connexion).
- **La commande `epiplan` :** l'installateur écrit un petit lanceur `epiplan.cmd` dans
  `%LOCALAPPDATA%\epiplan-bin\` et ajoute ce dossier à ton `PATH` utilisateur. Taper `epiplan` lance
  ce `.cmd`, qui appelle simplement le `python.exe` du venv sur `epiplan.py`. (Ouvre un **nouveau**
  terminal après l'installation pour que le PATH soit pris en compte.)
- **Réglages et données** vont sous ton profil : `C:\Users\<toi>\.config\epiplan\` et
  `C:\Users\<toi>\.local\share\epiplan\`.
- **L'interface** utilise `windows-curses`, donc elle s'affiche dans Windows Terminal, PowerShell ou cmd.
- **La connexion** ouvre ton Google Chrome installé ; `epiplan login --paste` marche si tu préfères
  coller le cookie.
- **Les notifications** s'affichent en bulles toast Windows (via PowerShell).
- **Les rappels en arrière-plan** (`epiplan notifications on`) créent une tâche du **Planificateur de
  tâches** qui lance `epiplan notify` chaque minute avec `pythonw.exe` (aucune fenêtre console ne
  clignote), même l'agenda fermé. `epiplan notifications off` la supprime.

Nécessite Python 3 (coche « Add to PATH » à son installation) et, pour la connexion en un clic, Google Chrome.

## Où sont tes données

- Réglages : `~/.config/epiplan/config.json` (Windows : `C:\Users\<toi>\.config\epiplan\config.json`)
- Agenda + session intra : `~/.local/share/epiplan/` (Windows : `C:\Users\<toi>\.local\share\epiplan\`)

Seul ton compte peut les lire. Rien n'est envoyé ailleurs qu'à intra.epitech.eu.

Si la variable d'environnement `EPITECH_TOKEN` est définie, epiplan l'utilise au lieu de se connecter. Évite-la
sur une machine partagée : les variables d'environnement sont visibles par les autres programmes que tu lances, et
finissent dans l'historique du shell ou les dotfiles. La connexion normale garde le jeton dans un fichier que toi
seul peux lire.

## Prérequis

Python 3, et (pour la connexion en un clic) Google Chrome. Fonctionne sous Linux, macOS et Windows.
L'installateur gère les extras (`windows-curses` sous Windows, Playwright pour la connexion). Sans
Chrome/Playwright, `epiplan login --paste` fonctionne partout.
