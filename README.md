# 🤖 JARVIS Local

**Votre assistant vocal personnel, façon Iron Man, qui tourne sur votre PC Windows.**

Vous dites « Jarvis » ou vous cliquez sur l'orbe. JARVIS vous répond à la voix,
avec son flegme de majordome britannique, et il sait vraiment faire des choses :

- 🗣️ **Conversation vocale en temps réel** : vous parlez, il répond aussitôt
- 👂 **Mains libres** : dites « Jarvis » (ou « Jarvis, ouvre Discord ») pour le
  réveiller ; il se remet en veille tout seul quand vous ne lui parlez plus
- ⌨️ **Ou par écrit** : le champ texte (Ctrl+J) envoie votre demande sans micro
- 🛠️ **Vraies tâches sur votre machine** : il lance des sessions **Claude Code**
  (chercher sur internet, analyser des fichiers, écrire du code, créer des
  documents…), montre leur progression et vous résume le résultat à voix haute
- 🔁 **Suite de tâches** : « Et maintenant, fais pareil pour mars » reprend la
  même session Claude, avec tout son contexte
- ⚡ **Actions instantanées**, sans attendre Claude : volume, musique,
  verrouillage du PC, presse-papiers, capture d'écran, ouverture d'applications
  et de sites (multi-écrans géré)
- ⏰ **Rappels, minuteurs et routines** : « Rappelle-moi dans 10 minutes de
  sortir le linge », « Tous les lundis et jeudis à 18 h, rappelle-moi le
  sport ». Un rappel arrivé se reporte d'un clic (**+10 min**, **+1 h**,
  **Demain**) ou à la voix (« reporte-le de 10 minutes »)
- ☀️ **Briefing du matin**, préparé sur votre PC et sans frais : rappels du
  jour, agenda A.R.E.S et météo (et, si vous le voulez, les titres de
  l'actualité)
- 💶 **Coût du jour et plafond** : « Aujourd'hui ≈ 0,42 $ » en haut de
  l'écran, et un plafond de dépense par jour si vous le souhaitez
- 🧠 **Mémoire et journal** : « Retiens que ma fille s'appelle Léa »,
  « De quoi on a parlé hier ? »
- 📅 **A.R.E.S** : si vous utilisez A.R.E.S, JARVIS lit et complète son agenda,
  ses tâches et ses notes (« Qu'est-ce que j'ai aujourd'hui ? »)
- 🌦️ **Météo et actualités** instantanées
- 👁️ **Vision** : « Regarde mon écran, tu vois cette erreur ? » ou la webcam
- 📊 **Rapports** : indicateurs, graphiques interactifs et tableaux à l'écran
- 💡 **JARVIS a remarqué** : quelques suggestions discrètes dans le panneau
  (tâches A.R.E.S en retard, tâche Claude échouée, rappels arrivés en retard)

> ⚠️ Pensé pour **Windows** : c'est là que tout fonctionne (fenêtre unique,
> raccourci global, icône de notification, multi-écrans, touches multimédia,
> démarrage automatique). Sur Mac et Linux, la voix, les tâches, les rappels,
> la mémoire, la vision et les rapports marchent aussi.

---

## 💰 Combien ça coûte ?

Deux services payants sont nécessaires :

| Service | Sert à | Coût approximatif |
|---|---|---|
| **Claude** (Anthropic) | Les tâches (fichiers, code, recherches) | Abonnement Claude Pro (~20 $/mois). Si vous utilisez déjà Claude Code, vous l'avez déjà |
| **API OpenAI** | La voix temps réel | Paiement à l'usage : comptez quelques dizaines de centimes pour 10 minutes de conversation. 5 $ de crédit suffisent pour découvrir |

- Le mot d'éveil et la mise en veille automatique évitent de laisser une
  conversation payante ouverte pour rien.
- En haut de l'écran, **« Aujourd'hui ≈ … $ »** suit la dépense du jour ; un
  clic ouvre **Réglages › Coûts** (les 30 derniers jours, voix et Claude).
  La part de Claude est une **estimation** : Claude Code la calcule au tarif de
  l'API, alors qu'avec un abonnement claude.ai les tâches ne sont pas
  facturées à l'unité.
- **Plafond du jour** (Réglages › Coûts, voix et tâches comprises) : JARVIS
  vous prévient à 80 %. Une fois le plafond atteint, le mot d'éveil n'ouvre
  plus de conversation, un clic sur l'orbe demande d'abord votre accord, aucune
  nouvelle tâche Claude ne démarre, et le briefing du matin est lu par la voix
  du navigateur, gratuite.
- Pour payer moins cher, choisissez **gpt-realtime-2.1-mini** dans
  Réglages › Voix.

---

## 📋 Étape 0 — Les prérequis

### Python (3.10 ou plus récent)

1. Allez sur https://www.python.org/downloads/ et cliquez sur **Download Python**
2. Lancez l'installeur. **IMPORTANT : cochez la case « Add Python to PATH »**
   en bas de la première fenêtre avant de cliquer sur Install
3. Vérifiez : ouvrez un terminal (touche Windows → tapez `cmd` → Entrée) et tapez :
   ```
   python --version
   ```
   Vous devez voir `Python 3.x.x`. Si « python n'est pas reconnu », réinstallez
   en cochant bien la case PATH.

### Google Chrome (conseillé) ou Microsoft Edge

JARVIS s'ouvre dans sa propre fenêtre grâce à l'un de ces deux navigateurs.
**Chrome est conseillé** : il sait reconnaître le mot d'éveil « Jarvis » en
français **sur le PC, sans rien envoyer** (il télécharge son module de langue
au premier clic sur l'orbe). Sinon, et avec Edge, la reconnaissance passe par
les serveurs de Google ou de Microsoft ; la barre d'état indique « écoute
locale » ou « écoute via Google ».

---

## 🧠 Étape 1 — Créer un compte Claude et installer Claude Code

Claude Code est l'agent qui exécute les vraies tâches sur votre machine.

1. **Créez un compte Claude** sur https://claude.ai (bouton *Sign up*)
2. **Prenez un abonnement Claude Pro** (nécessaire pour Claude Code) : sur
   https://claude.ai, paramètres → *Upgrade*
3. **Installez Claude Code** avec l'installeur officiel. Ouvrez **PowerShell**
   (touche Windows → tapez `powershell` → Entrée) et tapez :
   ```
   irm https://claude.ai/install.ps1 | iex
   ```
4. **Connectez-le à votre compte** :
   ```
   claude
   ```
   La première fois, il ouvre votre navigateur pour vous connecter. Suivez les
   instructions, puis tapez `/exit` pour quitter.
5. **Vérifiez** que tout marche :
   ```
   claude -p "Dis bonjour"
   ```
   Si Claude vous répond dans le terminal, c'est gagné. ✅

JARVIS a besoin de Claude Code **2.1.259 ou plus récent** : `claude update` le
met à jour (le bilan de santé de JARVIS vous le dit sinon).

---

## 🔑 Étape 2 — Créer un compte OpenAI et récupérer une clé API

La clé API OpenAI sert à la **voix temps réel**. Ce n'est PAS un abonnement
ChatGPT Plus : c'est un compte développeur, facturé à l'usage.

1. **Créez un compte** sur https://platform.openai.com/signup
2. **Ajoutez du crédit** sur https://platform.openai.com/settings/organization/billing
   → *Add payment method*, puis achetez du crédit (5 $ suffisent pour
   commencer). ⚠️ Sans crédit, la voix ne fonctionne pas (erreur « quota »).
3. **Créez votre clé** sur https://platform.openai.com/api-keys
   → *Create new secret key* → nommez-la (par exemple « jarvis ») → *Create*
4. **Copiez la clé tout de suite** (elle commence par `sk-`) : elle ne sera
   plus jamais affichée. Gardez-la secrète, comme un mot de passe.

---

## ⚙️ Étape 3 — Installer JARVIS

1. **Téléchargez ce dossier** (bouton vert *Code* → *Download ZIP* sur GitHub,
   puis décompressez-le où vous voulez), ou avec git : `git clone …`
2. **Ouvrez un terminal dans le dossier** : dans l'Explorateur Windows, ouvrez
   le dossier, cliquez dans la barre d'adresse, tapez `cmd` et appuyez sur Entrée
3. **Installez les dépendances Python** :
   ```
   pip install -r requirements.txt
   ```

La clé OpenAI se colle à l'étape suivante, dans la **Mise en route** (ou plus
tard dans Réglages › Connexion). Vous pouvez aussi la mettre dans un fichier
`.env` : copiez `.env.example` en `.env` et collez la clé après
`OPENAI_API_KEY=`.

---

## 🚀 Étape 4 — Lancer JARVIS

**Le plus simple : double-cliquez sur `JARVIS.bat`.** JARVIS démarre sans
fenêtre noire et s'ouvre dans sa propre fenêtre, comme une application. Un
second lancement ramène simplement la fenêtre déjà ouverte.

Ou, dans le terminal, toujours dans le dossier :

```
python server.py --app
```

(Sans `--app`, ouvrez vous-même Chrome sur **http://127.0.0.1:8788**.)

La première fois, la **Mise en route** vérifie tout avec vous : la clé
OpenAI, Claude Code, le micro, le mot d'éveil, A.R.E.S et les notifications.
Ensuite, dites **« Jarvis »**… et parlez !

- L'orbe indique **En veille** quand JARVIS attend son nom, et il vous écoute
  dès que la conversation est ouverte. Un clic sur l'orbe (ou Espace) fait
  l'un ou l'autre à la main.
- **Réglages** (en haut à droite) regroupe tout : voix, écoute, heures calmes,
  briefing, Claude Code, coûts, système, données, et le **bilan de santé**
  (Réglages › Connexion).
- **Démarrer avec Windows** : Réglages › Système, ou
  `python server.py --autostart on` (`off` pour arrêter).
- Pour quitter : icône JARVIS dans la zone de notification → **Quitter JARVIS**.

### Exemples de commandes vocales

- « Jarvis, présente-toi. »
- « Jarvis, ouvre Discord. » / « Mets Chrome sur l'écran de droite. »
- « Monte le son. » / « Pause. » / « Verrouille le PC. »
- « Rappelle-moi dans vingt minutes de sortir le linge. » → « Reporte-le de 10 minutes. »
- « Tous les lundis et jeudis à 18 h, rappelle-moi le sport. »
- « Le 12 octobre à 9 h, rappelle-moi le contrôle technique. »
- « Quel temps fera-t-il demain à Laon ? » / « Quels sont les titres de l'actualité ? »
- « Qu'est-ce que j'ai aujourd'hui ? » / « Note que je dois rappeler le garage. » (A.R.E.S)
- « Combien j'ai dépensé aujourd'hui ? »
- « Retiens que je préfère le thé au café. » / « De quoi on a parlé hier ? »
- « Regarde mon écran : tu comprends cette erreur ? »
- « Cherche les dernières actus IA et fais-moi un résumé. » → « Et maintenant,
  fais-en un document Word. »
- « Analyse le fichier ventes.xlsx et fais-moi un tableau de bord. »
- « Qu'est-ce qui tourne en ce moment ? » / « Annule la tâche. »
- « Merci, ce sera tout. » → JARVIS se remet en veille.

Le bouton **Aide** (ou la touche `?`) affiche « Ce que je sais faire » : chaque
exemple est un bouton qui le demande pour de vrai.

---

## ⌨️ Raccourcis

| Touche | Action |
|---|---|
| **Ctrl+Alt+Maj+J** | Depuis n'importe quelle application : ramène JARVIS et ouvre ou ferme la conversation (modifiable dans Réglages › Système) |
| **Espace** | Ouvrir ou fermer la conversation (quand le curseur n'est pas dans un champ) |
| **Ctrl+J** ou **/** | Écrire à JARVIS |
| **Échap** | Interrompre JARVIS, ou fermer la fenêtre ouverte |
| **Ctrl+M** | Couper ou rétablir le micro |
| **?** | Ce que je sais faire |

---

## ⏰ Rappels, routines et briefing du matin

- **Rappels** : à la voix (« dans un quart d'heure », « à midi », « mardi
  prochain à 9 h », « le 12/10 à 18 h »), ou depuis le panneau de droite. Un
  rappel peut revenir chaque jour, en semaine, chaque semaine, chaque mois ou
  certains jours (« tous les lundis et jeudis »).
- **Un rappel arrivé** s'affiche avec **+10 min**, **+1 h**, **Demain** et
  **Fait**. À la voix : « reporte-le d'une heure ».
- **Modifier** : le crayon ✎ du panneau change le texte, la date et l'heure.
- **Routines** : une tâche Claude programmée (« tous les matins à 8 h,
  cherche… »). Une routine avec **accès complet** demande votre « oui » à sa
  création, et sa consigne ne se modifie plus ensuite. Une routine manquée de
  plus de deux heures (PC éteint) n'est pas lancée en retard : JARVIS vous le
  dit et la reprogramme.
- **Briefing du matin** (Réglages › Proactivité : heure, jours, actualités) :
  préparé sur votre PC, sans tâche Claude : les rappels du jour, le nombre
  d'éléments de l'agenda A.R.E.S (jamais leurs titres) et la météo de votre
  ville. Avec « Ajouter les titres de l'actualité », les titres viennent des
  flux RSS.
- **Heures calmes** (22 h 30 à 7 h 30 par défaut) et **Ne pas déranger** :
  rien n'est dit à voix haute. Un rappel que vous avez programmé donne une
  notification silencieuse ; le reste, briefing compris, attend derrière la
  pastille de l'orbe.

---

## 🤝 Coexistence avec A.R.E.S

JARVIS est **la voix** ; A.R.E.S est **l'intendant** (agenda, tâches, notes).
Les deux s'entendent bien à condition de ne pas répondre ensemble :

- **A.R.E.S répond aussi à « Hey Jarvis »** (son mot d'éveil par défaut).
  Dans A.R.E.S › Réglages › Voix, **laissez le mode mains-libres désactivé**,
  ou choisissez le moteur **Vosk** avec le mot **« Arès »**.
- **Ne gardez jamais deux conversations vocales ouvertes** en même temps (une
  dans A.R.E.S, une dans JARVIS) : elles s'entendraient et coûteraient double.
- **Activez le serveur MCP local d'A.R.E.S** : A.R.E.S › Réglages ›
  Application de bureau (adresse `127.0.0.1:6178`). JARVIS le trouve tout
  seul ; Réglages › Système › A.R.E.S le règle sur Automatique, Toujours ou
  Jamais.
- Ce serveur n'a **pas de mot de passe** : seuls les programmes de votre PC
  peuvent le joindre. Si une version d'A.R.E.S propose un jeton MCP,
  activez-le et mettez-le dans `JARVIS_ARES_TOKEN`.
- A.R.E.S utilise les raccourcis Ctrl+Alt+V, M, J, K et Espace ; JARVIS prend
  **Ctrl+Alt+Maj+J** pour ne pas les gêner.

---

## 🔒 Sécurité — à lire

**Ce que Claude a le droit de faire.** Chaque tâche reçoit l'un de trois
profils, une **liste blanche** d'outils ; JARVIS choisit le plus restreint qui
suffit et l'affiche sur la carte de la tâche :

| Profil | Peut | Ne peut pas |
|---|---|---|
| **Web uniquement** (`recherche`) | Chercher et lire sur internet | Lire vos fichiers, lancer des commandes, écrire, utiliser vos connecteurs |
| **Lecture seule** (`lecture`, par défaut) | Lire et analyser vos fichiers | Modifier quoi que ce soit, lancer des commandes, aller sur internet |
| **Accès complet** (`complet`) | Créer et modifier des fichiers, lancer des commandes, internet, connecteurs | — |

- Une page web piégée lue pendant une recherche ne peut donc ni fouiller vos
  fichiers ni lancer de commande, et ce que JARVIS sait de vous (mémoire,
  agenda, rappels) n'est jamais mis dans une tâche qui va sur internet.
- **Accès complet = votre confirmation** : JARVIS vous demande « oui » à voix
  haute, ou un clic sur **Lancer**, avant chaque tâche ou routine avec accès
  complet. Après avoir lu une page web, une note ou votre écran, il redemande
  avant d'ouvrir un site inconnu, d'écrire dans le presse-papiers ou dans
  A.R.E.S.
- **Mode `auto`** : les tâches tournent sans personne pour répondre. Ce qui
  demanderait une autorisation est **refusé** (la tâche ne reste pas bloquée) ;
  le mode `auto` laisse passer ce qui est sûr et refuse le risqué. Le mode
  `bypassPermissions` (aucun contrôle) reste possible dans Réglages › Claude
  Code, avec un avertissement : il est déconseillé.
- **Ce que les garde-fous ne couvrent pas** : en accès complet, un script que
  Claude écrit puis exécute agit avec **vos** droits, partout où vous en avez.
  Le dossier de travail `~/JARVIS-travail` est l'endroit où les tâches
  démarrent et rangent leur travail, **pas une barrière**.
- Votre clé OpenAI **ne quitte jamais votre PC** : la page ne reçoit qu'une
  clé temporaire de deux minutes. Ne partagez jamais votre fichier `.env`.
- JARVIS n'accepte que les requêtes de **sa propre page, sur ce PC**
  (vérification de l'hôte et de l'origine, plus un jeton secret recréé à
  chaque démarrage). Ne mettez **jamais** ce serveur sur internet.
- Plafond par tâche (2 $ par défaut, Réglages › Claude Code) et plafond par
  jour (Réglages › Coûts).

---

## 🔁 Ce qui part où

| Quoi | Où |
|---|---|
| Votre voix pendant une conversation | OpenAI |
| Votre voix en veille (mot d'éveil) | Reconnaissance locale de Chrome, ou Google si elle n'est pas disponible |
| Captures d'écran et caméra | OpenAI, seulement quand vous le demandez |
| Tâches | Claude (Anthropic) |
| Météo, actualités | Open-Meteo, les flux RSS choisis (sans rien sur vous) |
| Journal, mémoire, rappels, coûts | Ce PC, dossier `data` (Réglages › Données › Ouvrir le dossier data) |

---

## 📅 Échéances OpenAI

- OpenAI arrête **gpt-realtime le 20 janvier 2027** : JARVIS utilise déjà
  **gpt-realtime-2.1** par défaut. Si vous aviez choisi gpt-realtime ou
  gpt-realtime-mini, passez à gpt-realtime-2.1 dans Réglages › Voix.
- OpenAI arrête **whisper-1 (et les transcripteurs gpt-4o) le 26 février 2027** :
  ils écrivent à l'écran ce que vous dites. Le remplaçant sera testé et
  proposé par une mise à jour de JARVIS avant cette date.
- Moins cher : **gpt-realtime-2.1-mini**.
- Le bilan de santé (Réglages › Connexion) vous le rappelle quand une date approche.

---

## 🔄 Mettre à jour

1. **JARVIS** : `git pull` dans le dossier, ou téléchargez à nouveau le ZIP et
   remplacez les fichiers (gardez votre `.env` et votre dossier `data`).
2. **Ses dépendances** : `pip install -r requirements.txt`
3. **Claude Code** : `claude update`
4. Relancez JARVIS (icône de notification → Quitter, puis `JARVIS.bat`).

---

## 🔌 Connecteurs (mails, agenda, Notion, domotique…)

Les connecteurs **MCP** de Claude Code ne servent qu'aux tâches avec **accès
complet** (donc après votre confirmation) : une recherche web ou une lecture
de fichiers n'y a jamais accès.

```
claude mcp add --scope user <nom> -- <commande du serveur MCP>
claude mcp add --scope user --transport http <nom> <adresse https du serveur>
claude mcp list
```

Vous pouvez aussi réserver des connecteurs à JARVIS seul : un fichier JSON au
format de Claude Code, indiqué dans Réglages › Claude Code › Connecteurs MCP
en plus (ou `JARVIS_MCP_CONFIG`) :

```json
{
  "mcpServers": {
    "mon-connecteur": { "command": "npx", "args": ["-y", "nom-du-paquet"] }
  }
}
```

---

## 🔧 Personnalisation

Presque tout se règle dans **Réglages**. Le fichier `.env` (voir
`.env.example`, chaque ligne y est expliquée) sert aux mêmes réglages au
démarrage :

| Variable | Défaut | Rôle |
|---|---|---|
| `OPENAI_API_KEY` | *(requis)* | Clé API OpenAI pour la voix |
| `REALTIME_MODEL` | `gpt-realtime-2.1` | Modèle vocal (`gpt-realtime-2.1-mini` coûte moins cher) |
| `JARVIS_VOICE` | `ballad` | Voix (ballad = majordome ; ash, echo, verse, cedar, marin…) |
| `JARVIS_WORKDIR` | `~/JARVIS-travail` | Dossier de travail des tâches Claude Code |
| `JARVIS_PERMISSION_MODE` | `auto` | Autorisations des tâches : `auto`, `acceptEdits`, `dontAsk`, `bypassPermissions` (déconseillé) |
| `JARVIS_TASK_TIMEOUT` | `600` | Durée maximale d'une tâche (secondes) |
| `JARVIS_TASK_BUDGET_USD` | `2.0` | Plafond par tâche, en dollars |
| `JARVIS_DAILY_BUDGET_USD` | `0` | Plafond par jour, voix et tâches (0 = aucun) |
| `JARVIS_WAKE_WORD` | `1` | Mot d'éveil « Jarvis » actif au démarrage |
| `JARVIS_IDLE_MINUTES` | `3` | Mise en veille après X minutes sans parler (0 = jamais) |
| `JARVIS_BRIEFING_TIME` | `08:00` | Heure du briefing du matin (vide = pas de briefing) |
| `JARVIS_BRIEFING_DAYS` | `lun-ven` | Jours du briefing (`tous`, `lun,mer,ven`…) |
| `JARVIS_BRIEFING_NEWS` | `0` | Ajouter les titres de l'actualité au briefing |
| `JARVIS_QUIET_HOURS` | `22:30-07:30` | Heures calmes : rien à voix haute |
| `JARVIS_CITY` | *(aucune)* | Votre ville, pour la météo |
| `JARVIS_HOTKEY` | `ctrl+alt+shift+j` | Raccourci global (Ctrl+Alt+Maj+J) |
| `JARVIS_ARES` | `auto` | A.R.E.S : `auto`, `on` ou `off` |
| `JARVIS_DATA_DIR` | `data` | Où JARVIS range sa mémoire, ses rappels, son journal et ses coûts |
| `JARVIS_PORT` | `8788` | Port du serveur local |

---

## ❓ Problèmes fréquents

Commencez par **Réglages › Connexion › Revérifier** : le bilan de santé dit ce
qui ne va pas et comment le corriger.

**« Clé OpenAI absente » ou « refusée »**
→ Collez votre clé dans Réglages › Connexion (ou vérifiez-la sur
platform.openai.com/api-keys).

**« Crédit OpenAI épuisé » (`insufficient_quota`, `429`)**
→ Ajoutez du crédit : étape 2, point 2.

**« Claude Code est introuvable » sur les tâches**
→ Refaites l'étape 1 (`irm https://claude.ai/install.ps1 | iex` dans
PowerShell), puis vérifiez avec `claude -p "test"`.

**« Plafond du jour atteint »**
→ Relevez le plafond dans Réglages › Coûts, ou attendez demain.

**Le micro ne marche pas**
→ Cadenas à gauche de l'adresse → Micro → Autoriser. Vérifiez aussi Windows ›
Confidentialité › Microphone › « Autoriser les applications de bureau ».

**JARVIS n'entend pas « Jarvis »**
→ Le mot d'éveil a besoin de Chrome ou d'Edge. Vérifiez qu'il est activé (le
bouton sous l'orbe). En attendant, un clic sur l'orbe ou Espace marche toujours.

**Deux voix répondent à « Jarvis »**
→ A.R.E.S écoute aussi : voir « Coexistence avec A.R.E.S ».

**Les graphiques ne s'affichent pas**
→ Ils se chargent en ligne : les chiffres restent dans le tableau en dessous.

**Python ou pip « n'est pas reconnu »**
→ Réinstallez Python en cochant « Add Python to PATH ».

**Double-cliquer sur `JARVIS.bat` n'ouvre rien**
→ Lancez `python server.py --app` dans un terminal pour voir l'erreur, ou
lisez le journal `data\jarvis.log`.

---

## 🧪 Pour les développeurs

```
pip install -r requirements-dev.txt
python -m pytest                      # tests unitaires
python -m pytest -m e2e tests/e2e     # dans Chromium (Playwright)
```

Les tests remplacent Claude Code et OpenAI par des imitations : ils ne coûtent
rien et ne touchent ni à votre PC ni à votre dossier `data`.

## 📄 Licence

MIT.
