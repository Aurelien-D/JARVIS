# 🤖 JARVIS Local

**Ton assistant vocal personnel, façon Iron Man, qui tourne 100 % sur ton PC.**

Tu dis « Jarvis » ou tu cliques sur l'orbe. JARVIS te répond à la voix (avec son
flegme de majordome britannique), et il sait vraiment faire des choses :

- 🗣️ **Conversation vocale en temps réel** : tu parles, il répond instantanément
- 👂 **Mains libres** : dis « Jarvis » (ou « Jarvis, ouvre Discord ») pour le
  réveiller ; il se remet en veille tout seul quand tu ne lui parles plus
- 🛠️ **Vraies tâches sur ta machine** : il lance des sessions **Claude Code**
  (chercher sur internet, analyser des fichiers, écrire du code, créer des
  documents...), montre leur progression en direct et te résume le résultat à
  voix haute
- 🔁 **Suite de tâches** : « Et maintenant, fais pareil pour mars » reprend la
  même session Claude, avec tout son contexte
- ⚡ **Actions instantanées**, sans attendre Claude : volume, pause/musique
  suivante, verrouiller le PC, presse-papiers, capture d'écran
- ⏰ **Rappels, minuteurs et routines** : « Rappelle-moi dans 10 minutes de
  sortir le linge », « Tous les matins à 9h, fais-moi un point sur l'actu IA »
- ☀️ **Briefing du matin** : météo, agenda, rappels du jour et actualités
- 🧠 **Mémoire** : « Retiens que ma fille s'appelle Léa » ; JARVIS s'en
  souvient à chaque conversation
- 👁️ **Vision** : « Regarde mon écran, tu vois cette erreur ? » ou « Regarde
  ce que je te montre » (webcam)
- 🚀 **Lancer tes applications** : « Ouvre Discord », « Mets Spotify sur
  l'écran de gauche » (multi-écrans géré)
- 🌐 **Ouvrir des sites** : « Ouvre mes emails » → Gmail s'ouvre dans ton navigateur
- 📊 **Afficher des rapports** : cartes, indicateurs, graphiques interactifs et
  tableaux directement sur l'interface (« Analyse ce fichier Excel et
  montre-moi un rapport »)
- 🔌 **Connecteurs** : branche tes mails, ton agenda, Notion, ta domotique... à
  Claude Code, et JARVIS sait s'en servir
- ❌ **Annuler une tâche** à la voix ou d'un clic

> ⚠️ Pensé pour **Windows** : c'est là que tout fonctionne (lancement
> d'applications, multi-écrans, touches multimédia, démarrage automatique).
> Sur Mac et Linux, la voix, les tâches, les rappels, la mémoire, la vision et
> les rapports marchent aussi ; le reste dépend des outils du système.

---

## 💰 Combien ça coûte ?

Deux services payants sont nécessaires :

| Service | Sert à | Coût approximatif |
|---|---|---|
| **Claude** (Anthropic) | Les tâches (fichiers, code, recherches) | Abonnement Claude Pro (~20 $/mois) — si tu utilises déjà Claude Code, tu l'as déjà |
| **API OpenAI** | La voix temps réel | Paiement à l'usage : compte ~10-30 centimes pour 10 min de conversation. 5 $ de crédit suffisent largement pour découvrir |

Le mot d'éveil et la mise en veille automatique évitent de laisser une session
vocale ouverte pour rien. Pour payer encore moins, essaie
`REALTIME_MODEL=gpt-realtime-mini` dans le fichier `.env`.

---

## 📋 Étape 0 — Les prérequis

### Python (3.10 ou plus récent)

1. Va sur https://www.python.org/downloads/ et clique **Download Python**
2. Lance l'installeur. **IMPORTANT : coche la case "Add Python to PATH"** en bas
   de la première fenêtre avant de cliquer Install
3. Vérifie : ouvre un terminal (touche Windows → tape `cmd` → Entrée) et tape :
   ```
   python --version
   ```
   Tu dois voir `Python 3.x.x`. Si "python n'est pas reconnu", réinstalle en
   cochant bien la case PATH.

### Node.js (nécessaire pour installer Claude Code)

1. Va sur https://nodejs.org et télécharge la version **LTS**
2. Installe en cliquant Suivant partout
3. Vérifie dans un **nouveau** terminal :
   ```
   node --version
   ```

### Google Chrome ou Microsoft Edge

JARVIS s'ouvre dans sa propre fenêtre grâce à l'un de ces deux navigateurs, et
le mot d'éveil « Jarvis » utilise leur reconnaissance vocale. Edge est déjà
installé sur Windows.

---

## 🧠 Étape 1 — Créer un compte Claude et installer Claude Code

Claude Code est l'agent qui exécute les vraies tâches sur ta machine.

1. **Crée un compte Claude** sur https://claude.ai (bouton *Sign up*)
2. **Prends un abonnement Claude Pro** (nécessaire pour utiliser Claude Code) :
   sur https://claude.ai, va dans les paramètres → *Upgrade*
3. **Installe Claude Code** — dans un terminal :
   ```
   npm install -g @anthropic-ai/claude-code
   ```
4. **Connecte-le à ton compte** — toujours dans le terminal :
   ```
   claude
   ```
   La première fois, il ouvre ton navigateur pour te connecter à ton compte
   Claude. Suis les instructions, puis tape `/exit` pour quitter.
5. **Vérifie** que tout marche :
   ```
   claude -p "Dis bonjour"
   ```
   Si Claude te répond dans le terminal, c'est gagné. ✅

---

## 🔑 Étape 2 — Créer un compte OpenAI et récupérer une clé API

La clé API OpenAI sert à la partie **voix temps réel** (ce n'est PAS un
abonnement ChatGPT Plus — c'est un compte développeur, facturé à l'usage).

1. **Crée un compte** sur https://platform.openai.com/signup
   (tu peux utiliser un compte Google)
2. **Ajoute du crédit** : va sur https://platform.openai.com/settings/organization/billing
   → *Add payment method* puis achète du crédit (5 $ suffisent pour commencer).
   ⚠️ Sans crédit, la voix ne fonctionnera pas (erreur "quota").
3. **Crée ta clé API** : va sur https://platform.openai.com/api-keys
   → *Create new secret key* → donne-lui un nom (ex : "jarvis") → *Create*
4. **Copie la clé immédiatement** (elle commence par `sk-...`) : elle ne sera
   plus jamais affichée. Garde-la secrète, c'est comme un mot de passe.

---

## ⚙️ Étape 3 — Installer JARVIS

1. **Télécharge ce dossier** (bouton vert *Code* → *Download ZIP* sur GitHub,
   puis décompresse-le où tu veux)

2. **Ouvre un terminal dans le dossier** : dans l'Explorateur Windows, ouvre le
   dossier, clique dans la barre d'adresse, tape `cmd` et appuie sur Entrée

3. **Installe les dépendances Python** :
   ```
   pip install -r requirements.txt
   ```

4. **Configure ta clé** :
   - Fais une copie du fichier `.env.example` et renomme-la `.env`
     (dans le terminal : `copy .env.example .env`)
   - Ouvre `.env` avec le Bloc-notes et colle ta clé OpenAI après
     `OPENAI_API_KEY=` (sans espaces, sans guillemets) :
     ```
     OPENAI_API_KEY=sk-proj-ta-cle-ici
     ```
   - (Conseillé) Décommente `JARVIS_WORKDIR` et mets un dossier dédié où
     JARVIS travaillera sur tes fichiers
   - (Conseillé) Décommente `JARVIS_CITY` et mets ta ville, pour la météo du
     briefing du matin

---

## 🚀 Étape 4 — Lancer JARVIS

**Le plus simple : double-clique sur `JARVIS.bat`.** JARVIS démarre sans
fenêtre noire et s'ouvre dans sa propre fenêtre, comme une application.

Ou, dans le terminal, toujours dans le dossier :

```
python server.py --app
```

(Sans `--app`, ouvre toi-même ton navigateur sur **http://127.0.0.1:8788**.)

La première fois :

1. **Autorise le micro** quand la fenêtre le demande
2. Clique sur **« Activer les notifications »** (en bas) si tu veux être
   prévenu quand une tâche se termine pendant que tu fais autre chose
3. Dis **« Jarvis »**... et parle !

L'orbe affiche **EN VEILLE** quand JARVIS attend son nom, et **EN LIGNE**
quand il t'écoute. Un clic sur l'orbe fait l'un ou l'autre à la main. Le bouton
**MOT D'ÉVEIL** l'active ou le coupe.

### Démarrer JARVIS avec Windows

```
python server.py --autostart on
```

JARVIS se lancera tout seul à chaque démarrage du PC, en veille, prêt à
entendre son nom. Pour arrêter : `python server.py --autostart off`.

### Exemples de commandes vocales

- « Jarvis, présente-toi. »
- « Jarvis, ouvre Discord. » / « Mets Chrome sur l'écran de droite. »
- « Ouvre mes emails. »
- « Monte le son. » / « Pause. » / « Musique suivante. » / « Verrouille le PC. »
- « Lis ce que j'ai copié et résume-le. »
- « Rappelle-moi dans vingt minutes de sortir le linge. »
- « Tous les lundis à 9h, fais-moi un point sur les actus de mon secteur. »
- « Retiens que je préfère le thé au café. » / « Oublie ça. »
- « Regarde mon écran : tu comprends cette erreur ? »
- « Liste les fichiers de mon dossier de travail et dis-moi ce qu'il y a dedans. »
- « Cherche les dernières actus IA et fais-moi un résumé. » → puis « Et
  maintenant, fais-en un document Word. »
- « Affiche-moi un comparatif des 3 meilleurs GPU du moment dans un rapport. »
- « Qu'est-ce qui tourne en ce moment ? » / « Annule la tâche. »
- « Merci, ce sera tout. » → JARVIS se remet en veille.

Pour l'arrêter complètement : ferme la fenêtre de JARVIS, puis le terminal (ou
Ctrl+C dedans). S'il a été lancé par `JARVIS.bat` ou au démarrage, il tourne en
arrière-plan : redémarre le PC ou termine `pythonw.exe` dans le Gestionnaire des
tâches.

---

## 🔒 Ce que Claude a le droit de faire

Chaque tâche est lancée avec le **profil le plus restreint** qui suffit, choisi
par JARVIS :

| Profil | Peut | Ne peut pas |
|---|---|---|
| `recherche` | Chercher et lire sur internet, utiliser tes connecteurs | Lire tes fichiers, lancer des commandes, écrire |
| `lecture` | Lire et analyser tes fichiers | Modifier quoi que ce soit, lancer des commandes, aller sur internet |
| `complet` | Tout : créer et modifier des fichiers, lancer des commandes, internet | — |

Une page web piégée lue pendant une recherche ne peut donc ni fouiller tes
fichiers ni lancer de commande. Le profil de chaque tâche s'affiche sur sa
carte, à droite.

---

## 🔌 Connecteurs (mails, agenda, Notion, domotique...)

Les tâches de JARVIS sont des sessions Claude Code : tous les **connecteurs
MCP** que tu ajoutes à Claude Code deviennent utilisables par JARVIS (« Lis
mes mails importants », « Qu'est-ce que j'ai à l'agenda demain ? »), et le
briefing du matin s'en sert automatiquement.

Pour en ajouter un, dans un terminal (`--scope user` le rend disponible
partout, donc aussi dans le dossier de travail de JARVIS) :

```
claude mcp add --scope user <nom> -- <commande du serveur MCP>
claude mcp add --scope user --transport http <nom> <adresse https du serveur>
claude mcp list
```

La commande ou l'adresse à utiliser est donnée par la documentation de chaque
connecteur.

Tu peux aussi réserver des connecteurs à JARVIS seul : mets-les dans un
fichier JSON au format Claude Code, et indique son chemin dans
`JARVIS_MCP_CONFIG` :

```json
{
  "mcpServers": {
    "mon-connecteur": { "command": "npx", "args": ["-y", "nom-du-paquet"] }
  }
}
```

---

## 🔧 Personnalisation (fichier `.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `OPENAI_API_KEY` | *(requis)* | Clé API OpenAI pour la voix |
| `REALTIME_MODEL` | `gpt-realtime` | Modèle vocal OpenAI (`gpt-realtime-mini` coûte moins cher) |
| `JARVIS_VOICE` | `ballad` | Voix (ballad = majordome ; ash, echo, verse, cedar, marin...) |
| `JARVIS_LANGUAGE` | `français` | Langue parlée |
| `JARVIS_WORKDIR` | dossier utilisateur | Dossier de travail des sessions Claude Code |
| `JARVIS_TASK_TIMEOUT` | `600` | Durée max d'une tâche (secondes) |
| `JARVIS_PORT` | `8788` | Port du serveur local |
| `JARVIS_PERMISSION_MODE` | `bypassPermissions` | Autorisations des sessions Claude Code : `bypassPermissions` (aucune demande), `acceptEdits` (fichiers OK, shell refusé), `off` (comportement d'origine) |
| `JARVIS_MODEL_SIMPLE` | `haiku` | Modèle Claude des tâches simples |
| `JARVIS_MODEL_NORMAL` | `sonnet` | Modèle Claude des tâches normales |
| `JARVIS_MODEL_COMPLEX` | `opus` | Modèle Claude des tâches complexes (si ton abonnement n'y a pas accès, JARVIS réessaie avec ton modèle par défaut) |
| `JARVIS_MCP_CONFIG` | *(aucun)* | Fichier de connecteurs MCP réservés à JARVIS |
| `JARVIS_WAKE_WORD` | `1` | Mot d'éveil « Jarvis » actif au démarrage (`0` pour le couper) |
| `JARVIS_IDLE_MINUTES` | `3` | Mise en veille après X minutes sans parler (`0` = jamais) |
| `JARVIS_BRIEFING_TIME` | `08:00` | Heure du briefing du matin (vide = désactivé) |
| `JARVIS_CITY` | *(aucune)* | Ta ville, pour la météo du briefing |
| `JARVIS_DATA_DIR` | `data` | Où JARVIS range sa mémoire, ses rappels, l'historique et son journal |

---

## ❓ Problèmes fréquents

**« OPENAI_API_KEY manquant »**
→ Le fichier `.env` n'existe pas ou la clé n'est pas dedans. Vérifie qu'il
s'appelle bien `.env` (pas `.env.txt` — active l'affichage des extensions de
fichiers dans l'Explorateur) et relance JARVIS.

**Erreur `insufficient_quota` ou `429`**
→ Pas de crédit sur ton compte OpenAI. Retourne à l'étape 2, point 2.

**« La commande 'claude' est introuvable » sur les tâches**
→ Claude Code n'est pas installé ou pas connecté. Refais l'étape 1 et vérifie
avec `claude -p "test"` dans un terminal.

**Le micro ne marche pas**
→ Vérifie que tu as bien cliqué "Autoriser" sur la demande du navigateur.
Sinon : icône 🔒/⚙ à gauche de l'adresse → Micro → Autoriser, puis recharge.

**JARVIS n'entend pas « Jarvis »**
→ Le mot d'éveil a besoin de Chrome ou Edge et d'internet (c'est la
reconnaissance vocale du navigateur). Vérifie que le bouton **MOT D'ÉVEIL**
affiche ON. En attendant, un clic sur l'orbe marche toujours.

**L'orbe dit « erreur » à la connexion**
→ Lis le message affiché sous l'orbe : il contient la vraie raison (clé
invalide, pas de crédit, pas d'internet...).

**« Jeton de session invalide »**
→ JARVIS a redémarré pendant que la page était ouverte : recharge-la (F5).

**Double-cliquer sur `JARVIS.bat` n'ouvre rien**
→ Lance `python server.py --app` dans un terminal pour voir l'erreur, ou lis
le journal `data\jarvis.log`.

**Les rapports/graphiques ne s'affichent pas**
→ Il faut une connexion internet (les composants graphiques se chargent en ligne).

**Python ou pip « n'est pas reconnu »**
→ Python n'est pas dans le PATH. Réinstalle-le en cochant "Add Python to PATH".

---

## 🔒 Sécurité — à lire

- Ta clé OpenAI **ne quitte jamais ton PC** : le navigateur ne reçoit qu'un
  jeton temporaire de session. Ne partage jamais ton fichier `.env`.
- JARVIS n'accepte que les requêtes de **sa propre page, sur ce PC** : un
  site web malveillant ouvert dans ton navigateur ne peut pas lui envoyer de
  tâche (vérification de l'hôte et de l'origine, plus un jeton secret
  recréé à chaque démarrage). Ne mets **jamais** ce serveur sur internet.
- ⚠️ Les sessions Claude Code tournent **sans demande d'autorisation**
  (`JARVIS_PERMISSION_MODE=bypassPermissions`). C'est nécessaire : elles n'ont
  aucune interface, donc personne ne pourrait répondre à une demande — la tâche
  resterait bloquée jusqu'au timeout. Les profils `recherche` et `lecture`
  limitent ce que chaque tâche peut faire, mais une tâche `complet` peut
  écrire, supprimer et exécuter des commandes avec **tes** droits.
  `JARVIS_WORKDIR` est le dossier où elle démarre, **pas une barrière** : mets-y
  un dossier dédié pour qu'elle y range son travail, sans compter dessus pour
  protéger le reste. Si tu préfères un garde-fou plus strict, mets
  `JARVIS_PERMISSION_MODE=acceptEdits` : les commandes shell seront alors
  refusées au lieu d'être exécutées.
- Le **mot d'éveil** utilise la reconnaissance vocale de Chrome ou Edge : tant
  qu'il est actif, ce que capte le micro est transcrit par le service de
  Google ou de Microsoft. Coupe-le avec le bouton **MOT D'ÉVEIL** si tu
  préfères cliquer sur l'orbe.
- La **mémoire**, les rappels et l'historique des tâches restent sur ton PC,
  dans le dossier `data` (ou `JARVIS_DATA_DIR`). Tu peux tout effacer en
  supprimant ce dossier.
- Les sessions Claude Code utilisent **ton** compte et **ta** machine, avec les
  protections normales de Claude Code.

---

## 🧪 Pour les développeurs

```
pip install -r requirements.txt pytest
python -m pytest
```

Les tests remplacent Claude Code et OpenAI par des imitations : ils ne
coûtent rien et ne touchent pas à ton PC.

## 📄 Licence

MIT — fais-en ce que tu veux.
