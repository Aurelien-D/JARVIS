# 🤖 JARVIS Local

**Votre assistant vocal personnel, façon Iron Man, qui tourne sur votre PC Windows.**
*Mark IX.*

Vous dites « Jarvis » ou vous cliquez sur l'orbe. JARVIS vous répond à la voix,
avec son flegme de majordome britannique, et il sait vraiment faire des choses :

- 🗣️ **Conversation vocale en temps réel** : vous parlez, il répond aussitôt,
  et vous pouvez lui couper la parole
- 👂 **Mains libres** : dites « Jarvis » (ou « Jarvis, ouvre Discord ») pour le
  réveiller ; il se remet en veille tout seul quand vous ne lui parlez plus
- ⌨️ **Ou par écrit** : le champ texte sous l'orbe (Ctrl+J ou /) envoie votre
  demande sans micro ; `/tâche …` confie directement un travail à Claude, sans
  conversation vocale
- 🛠️ **Vraies tâches sur votre machine** : il lance des sessions **Claude Code**
  (chercher sur internet, analyser des fichiers, écrire du code, créer des
  documents…), montre leur progression et vous résume le résultat à voix haute
- 🔁 **Suite de tâches** : « Et maintenant, fais pareil pour mars » reprend la
  même session Claude, avec tout son contexte
- ⚡ **Actions instantanées**, sans attendre Claude : volume, musique,
  verrouillage du PC (3 secondes pour annuler), presse-papiers, capture
  d'écran, ouverture d'applications (Microsoft Store compris) et de sites,
  multi-écrans géré
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
- 🔕 **Heures calmes et « Ne pas déranger »** : la nuit ou en réunion, JARVIS
  se tait et garde ses messages pour plus tard

> ⚠️ Pensé pour **Windows** : c'est là que tout fonctionne (fenêtre unique,
> raccourci global, icône de notification, multi-écrans, touches multimédia,
> démarrage automatique). Sur Mac et Linux, la voix, les tâches, les rappels,
> la mémoire, la vision et les rapports marchent aussi.

**Dans ce guide** : les coûts · l'installation pas à pas (étapes 0 à 4) ·
Réglages · se servir de JARVIS · raccourcis · rappels et briefing ·
coexistence avec A.R.E.S · sécurité · ce qui part où · échéances OpenAI ·
mettre à jour · connecteurs · réglages avancés · problèmes fréquents · pour
les développeurs.

---

## 💰 Coûts et plafonds

Deux services payants sont nécessaires :

| Service | Sert à | Coût approximatif |
|---|---|---|
| **Claude** (Anthropic) | Les tâches (fichiers, code, recherches) | Abonnement Claude Pro (~20 $/mois) ou Max. Si vous utilisez déjà Claude Code, vous l'avez déjà |
| **API OpenAI** | La voix temps réel | Paiement à l'usage. 5 $ de crédit suffisent pour découvrir |

**Ordre de grandeur de la voix** (tarifs publics d'OpenAI, octobre 2026) :

- avec **gpt-realtime-2.1** (le modèle par défaut) : environ **0,02 $ par
  minute où vous parlez** et **0,08 $ par minute où JARVIS parle** ;
- avec **gpt-realtime-2.1-mini** : environ **trois fois moins cher**
  (Réglages › Voix › Modèle vocal).

Les silences ne sont pas facturés, mais chaque réponse relit la conversation
(à prix réduit) : une longue conversation revient un peu plus cher. Le mieux
est de regarder le compteur.

**Ce qui vous protège des mauvaises surprises :**

- Le **mot d'éveil** et la **mise en veille automatique** (après 3 minutes
  sans parler, réglable dans Réglages › Écoute) évitent de laisser une
  conversation payante ouverte pour rien. « Jarvis » suivi d'un silence se
  rendort au bout de 8 secondes.
- En haut de l'écran, **« Aujourd'hui ≈ … $ »** suit la dépense du jour (voix
  et Claude). Un clic ouvre **Réglages › Coûts** : les 30 derniers jours en
  graphique, et « Voir les chiffres » pour le tableau. À la voix : « Combien
  j'ai dépensé aujourd'hui ? »
- La part de Claude est une **estimation** : Claude Code la calcule au tarif
  de l'API, alors qu'avec un abonnement claude.ai les tâches ne sont pas
  facturées à l'unité.
- **Plafond par tâche** : 2 $ par défaut (Réglages › Claude Code). Claude
  s'arrête quand son estimation l'atteint. 0 = pas de plafond.
- **Plafond du jour** (Réglages › Coûts, aucun par défaut) : voix et tâches
  comprises. JARVIS vous prévient à 80 %. Une fois le plafond atteint :
  - le mot d'éveil n'ouvre plus de conversation ; un clic sur l'orbe (ou
    Espace, le champ texte, le raccourci global) demande d'abord votre accord
    (« Ouvrir quand même ») ;
  - aucune nouvelle tâche Claude ne démarre, et « JARVIS a remarqué » ne
    propose plus de relancer une tâche ;
  - le briefing du matin est lu en entier par la voix du navigateur, gratuite.

  Le compteur repart à zéro à minuit (heure du PC). Le plafond compte aussi
  l'estimation de Claude, même si votre abonnement ne la facture pas :
  prévoyez large.

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
au premier clic sur l'orbe, si votre PC le permet). Sinon, et toujours avec
Edge, l'écoute du mot d'éveil passe par les serveurs de Google ou de
Microsoft. La barre d'état indique « écoute locale » quand rien ne quitte le
PC, et « écoute via Google » dans l'autre cas (avec Edge, ce sont en fait les
serveurs de Microsoft).

### Git pour Windows (facultatif)

https://git-scm.com/download/win. Il permet d'installer et de mettre à jour
JARVIS avec `git`, et Claude Code s'en sert pour ses commandes (sans Git, il
utilise PowerShell). Vous pouvez vous en passer.

---

## 🧠 Étape 1 — Créer un compte Claude et installer Claude Code

Claude Code est l'agent qui exécute les vraies tâches sur votre machine.

1. **Créez un compte Claude** sur https://claude.ai (bouton *Sign up*)
2. **Prenez un abonnement Claude Pro** (ou Max), nécessaire pour Claude
   Code : sur https://claude.ai, paramètres → *Upgrade*
3. **Installez Claude Code** avec l'installeur officiel. Ouvrez **PowerShell**
   (touche Windows → tapez `powershell` → Entrée) et tapez :
   ```
   irm https://claude.ai/install.ps1 | iex
   ```
   Fermez ensuite PowerShell et ouvrez-en un nouveau, pour que la commande
   `claude` soit reconnue.

   *Autre méthode*, seulement si vous avez déjà Node.js 22 ou plus récent :
   `npm install -g @anthropic-ai/claude-code` installe le même programme.
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

JARVIS a besoin de Claude Code **2.1.259 ou plus récent** (`claude --version`
l'affiche) : `claude update` le met à jour, et le bilan de santé de JARVIS
vous le rappelle si besoin.

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

1. **Téléchargez JARVIS** : sur https://github.com/Aurelien-D/jarvis, bouton
   vert *Code* → *Download ZIP*, puis décompressez-le où vous voulez (par
   exemple dans `Documents\jarvis`). Avec Git :
   ```
   git clone https://github.com/Aurelien-D/jarvis.git
   ```
2. **Ouvrez un terminal dans le dossier** : dans l'Explorateur Windows, ouvrez
   le dossier, cliquez dans la barre d'adresse, tapez `cmd` et appuyez sur Entrée
3. **Installez les dépendances Python** :
   ```
   pip install -r requirements.txt
   ```
   (Si « pip n'est pas reconnu » : `py -m pip install -r requirements.txt`.)

La clé OpenAI se colle à l'étape suivante, dans la **Mise en route** (ou plus
tard dans Réglages › Connexion). Elle est rangée sur votre PC, dans un fichier
`.env` du dossier de JARVIS. Vous pouvez aussi créer ce fichier vous-même :
copiez `.env.example` en `.env` et collez la clé après `OPENAI_API_KEY=`.

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

La fenêtre de JARVIS vous demande l'accès au micro la première fois :
cliquez sur **Autoriser**.

### La Mise en route

Au premier lancement, la **Mise en route** s'ouvre toute seule et vérifie tout
avec vous, dans l'ordre : **Clé OpenAI** (collez-la ici), **Claude Code**
(installé, assez récent et connecté), **Micro** (bouton « Tester le micro »),
**Votre micro est…** (un casque, ou le micro du PC ou de la webcam : cela
règle la réduction de bruit), **Mot d'éveil** (écoute locale ou via Google),
**A.R.E.S** et **Notifications**. Viennent ensuite les autres vérifications
(dossier de travail, navigateur, raccourci global, échéances OpenAI…).

- **À corriger** empêche JARVIS de fonctionner normalement ; **Info** est
  seulement pour information. Chaque ligne dit quoi faire.
- « Tester la voix » fait parler la voix du navigateur, celle des annonces
  hors conversation.
- **Revérifier** refait les vérifications ; **Terminer** ferme la fenêtre.
  Elle se rouvrira d'elle-même si un nouveau problème apparaît.
- À tout moment : **Réglages › Connexion** montre le même **bilan de santé**
  (bouton **Revérifier**) et le bouton **Ouvrir la mise en route**.

Ensuite, dites **« Jarvis »**… et parlez !

### Au quotidien

- **Démarrer avec Windows** : Réglages › Système › « Lancer JARVIS au
  démarrage de Windows », ou dans un terminal `python server.py --autostart on`
  (`off` pour arrêter).
- **L'icône JARVIS** dans la zone de notification (près de l'horloge) offre :
  Ouvrir JARVIS, Parler, Mot d'éveil (activer ou couper), Ne pas déranger 1 h,
  Démarrer avec Windows et **Quitter JARVIS**.
- Pour quitter : icône JARVIS → **Quitter JARVIS**. Les tâches Claude en cours
  sont alors arrêtées (elles apparaissent « Interrompue »).
- Pendant qu'une tâche Claude tourne, JARVIS empêche le PC de se mettre en
  veille.

---

## 🎛️ Réglages : tout se règle à l'écran

Le bouton **Réglages** (en haut à droite) remplace l'édition du fichier `.env`
pour tous les réglages du quotidien. Chaque modification dit quand elle
s'applique : tout de suite, à la prochaine conversation, à la prochaine tâche,
ou au prochain démarrage de JARVIS.

| Section | Ce qu'on y trouve |
|---|---|
| **Connexion** | La clé OpenAI (Ajouter ou Remplacer la clé), le bilan de santé, Revérifier, Ouvrir la mise en route |
| **Voix** | Voix de JARVIS (ballad = majordome britannique), vitesse, modèle vocal, effort de réflexion |
| **Écoute** | Mot d'éveil « Jarvis », type de micro, prise de parole (s'il vous coupe trop tôt), mise en veille après 3 min, micro et sortie audio, « Maintenir Espace pour parler » |
| **Proactivité** | Briefing du matin (heure, jours, actualités), heures calmes, votre ville, rouvrir la fenêtre quand un rappel arrive |
| **Claude Code** | Mode de permission, dossier de travail, connecteurs MCP en plus, modèle Claude par complexité (haiku, sonnet, opus), durée maximale (10 min), plafond par tâche (2 $), tâches en même temps (3) |
| **Coûts** | Plafond de dépense par jour et les 30 derniers jours |
| **Système** | Lancer JARVIS au démarrage de Windows, raccourci global, navigateur, icône de notification, A.R.E.S (Automatique, Toujours, Jamais), animations, volume des sons |
| **Données** | Dossier des données (Ouvrir le dossier data, Purger le journal), conservation du journal (30 jours) |
| **À propos** | Versions de JARVIS et de Claude Code, modèles utilisés, mot d'éveil, échéances OpenAI |

- Les **réglages sensibles** (marqués d'un cadenas : mode de permission,
  dossier de travail, connecteurs MCP, clé OpenAI) demandent une confirmation
  et ne se changent **jamais à la voix**.
- Le mot d'éveil, le micro, la sortie audio, « Maintenir Espace pour
  parler », les animations et le volume des sons sont propres à ce navigateur.
- Ce que vous réglez ici est enregistré dans `data\settings.json` et
  l'emporte sur le fichier `.env` (voir « Réglages avancés »).

---

## 🗣️ Se servir de JARVIS

### L'écran

- **L'orbe**, au centre : un clic (ou Espace) ouvre la conversation, ou remet
  JARVIS en veille. La ligne d'état dit toujours ce qui se passe : *Hors
  ligne*, *En veille · dites « Jarvis »*, *Je vous écoute…*, *Réflexion…*,
  *JARVIS répond*, *Micro coupé*, *En attente de votre confirmation*.
- **Sous l'orbe** : le champ texte, puis, pendant une conversation, les
  boutons **Interrompre**, **Micro** et **Veille**. Toujours là : **Mot
  d'éveil** (activé ou désactivé) et **Ne pas déranger 1 h**.
- **En haut** : les pastilles (Ne pas déranger ou Heures calmes, A.R.E.S,
  « Aujourd'hui ≈ … $ »), puis les boutons **Aide**, **Journal**, **Panneau**
  (quand la fenêtre est étroite), **Réglages** et, après un rapport,
  **Dernier rapport**.
- **Le panneau de droite** : *JARVIS a remarqué*, l'agenda A.R.E.S, les
  **sessions Claude Code** (Copier, Continuer, Réessayer, Annuler la tâche ;
  **Lire** ouvre sa fiche : résultat, fichiers créés avec « Afficher dans
  l'explorateur », refus, étapes suivies, coût estimé), les **rappels &
  routines** (✎ pour modifier, ✕ pour supprimer, avec « Annuler ») et la
  **mémoire** (✎ et ✕ de même).
- **Le Journal** : vos conversations jour par jour, avec une recherche. Il est
  gardé 30 jours sur ce PC (Réglages › Données) ; « Effacer l'historique » le
  vide.
- **Une pastille sur l'orbe** signale des messages en attente (une tâche
  finie ou le briefing pendant une heure calme…) : cliquez dessus pour les
  écouter.

### Exemples de commandes vocales

- **Applications et PC** : « Jarvis, ouvre Spotify sur l'écran de gauche. »
  · « Baisse le volume à 30 %. » · « Pause. » · « Verrouille le PC. »
- **Rappels et routines** : « Rappelle-moi dans 20 minutes de sortir le pain. »
  · « Reporte le rappel de 10 minutes. » · « Tous les lundis et jeudis à
  18 h, rappelle-moi le sport. » · « Le 12 octobre à 9 h, rappelle-moi le
  contrôle technique. »
- **Recherche et fichiers** (tâches Claude) : « Cherche les meilleurs
  aspirateurs robots sous 400 €. » → « Et maintenant, fais-en un document
  Word. » · « Analyse le fichier ventes.xlsx et fais-moi un tableau de bord. »
- **Météo et actualités** : « Quel temps fera-t-il demain à Laon ? » · « Quels
  sont les titres de l'actualité ? »
- **Vision** : « Regarde mon écran : tu vois l'erreur ? » · « Regarde-moi avec
  la caméra : je suis bien coiffé ? »
- **Mémoire et journal** : « Retiens que je préfère le thé. » · « De quoi on a
  parlé hier ? »
- **Point du jour** : « Qu'est-ce qui tourne en ce moment ? » · « Combien j'ai
  dépensé aujourd'hui ? » · « Annule la tâche. »
- **Agenda A.R.E.S** : « Qu'est-ce que j'ai aujourd'hui ? » · « Note que je
  dois rappeler le garage. »
- « Merci, ce sera tout. » → JARVIS se remet en veille.

Le bouton **Aide** (ou la touche `?`) affiche « Ce que je sais faire » : chaque
exemple est un bouton qui le demande pour de vrai.

Le nom doit **commencer** la phrase (« Jarvis, … », « Dis Jarvis… ») : parler
*de* JARVIS ne le réveille pas.

---

## ⌨️ Raccourcis

| Touche | Quand | Action |
|---|---|---|
| **Ctrl+Alt+Maj+J** | Depuis n'importe quelle application (Windows) | Ramène JARVIS au premier plan et ouvre la conversation, ou la remet en veille si elle était ouverte. Modifiable dans Réglages › Système |
| **Espace** | Quand le curseur n'est ni dans un champ ni sur un bouton (ou sur l'orbe) | Ouvrir ou fermer la conversation (comme un clic sur l'orbe) |
| **Maintenir Espace** | Pendant une conversation, si « Maintenir Espace pour parler » est coché (Réglages › Écoute) | JARVIS ne vous écoute que tant que vous maintenez la touche |
| **Ctrl+J** ou **/** | Hors d'un champ de saisie (Ctrl+J marche aussi depuis le champ texte) | Écrire à JARVIS |
| **Entrée** | Dans le champ texte | Envoyer |
| **↑** / **↓** | Dans le champ texte | Retrouver vos 20 derniers messages |
| **Échap** | Partout | Dans l'ordre : annule un verrouillage en cours, ferme la fenêtre ou le panneau ouvert, interrompt JARVIS s'il parle ou travaille, coupe une annonce de la voix du navigateur |
| **Ctrl+M** | Pendant une conversation, même dans un champ | Couper ou rétablir le micro |
| **?** | Quand le curseur n'est dans aucun champ | Ce que je sais faire |

Sur un clavier AZERTY, `?` se tape avec Maj+, et `/` avec Maj+: (ou le `/`
du pavé numérique). Si Ctrl+J ouvre les téléchargements de Chrome au lieu du
champ texte, utilisez `/`.

Le raccourci global ne peut pas reprendre ceux d'A.R.E.S (Ctrl+Alt+V, M, J, K
et Espace). S'il est déjà utilisé par un autre programme, JARVIS vous le dit
et vous en choisissez un autre dans Réglages › Système.

---

## ⏰ Rappels, routines et briefing du matin

- **Rappels** : à la voix (« dans un quart d'heure », « à midi », « mardi
  prochain à 9 h », « le 12/10 à 18 h », « demain soir à 8 h »), ou depuis le
  panneau de droite. Un rappel peut revenir chaque jour, en semaine, chaque
  semaine, chaque mois ou certains jours (« tous les lundis et jeudis »).
- **Un rappel arrivé** s'affiche avec **+10 min**, **+1 h**, **Demain** et
  **Fait**. À la voix : « reporte-le d'une heure », « reporte-le à demain ».
  Si la fenêtre de JARVIS est fermée (JARVIS toujours lancé), une notification
  Windows s'affiche et la fenêtre se rouvre, sauf en heure calme ou devant un
  plein écran (Réglages › Proactivité pour l'éviter).
- **Modifier** : le crayon ✎ du panneau change le texte, la date et l'heure.
- **JARVIS fermé ou PC éteint** : un rappel manqué arrive au démarrage
  suivant, avec son retard. Une **routine** manquée de plus de deux heures
  n'est pas lancée en retard : JARVIS vous le dit et la reprogramme.
- **Routines** : une tâche Claude programmée (« tous les matins à 8 h,
  cherche… »). Une routine avec **accès complet** demande votre « oui » à sa
  création, et sa consigne ne se modifie plus ensuite.
- **Briefing du matin** (Réglages › Proactivité : heure, 8 h par défaut ;
  jours, du lundi au vendredi par défaut ; actualités) : préparé sur votre PC,
  sans tâche Claude. Il donne la date, vos rappels du jour, le nombre
  d'éléments de l'agenda A.R.E.S aujourd'hui et en retard (jamais leurs
  titres) et la météo de votre ville. Avec « Ajouter les titres de
  l'actualité », trois titres viennent des flux RSS (Le Monde par défaut) ;
  si les flux sont illisibles, une tâche Claude « Web uniquement » les
  cherche, avec la date pour seule information.
- **Comment il arrive** : une carte « Briefing du matin » s'affiche, et la
  voix du navigateur vous dit qu'il est prêt ; appelez JARVIS pour qu'il vous
  le présente (en pleine conversation, il le présente aussitôt). Si le PC est
  allumé plus tard, le briefing arrive encore dans les quatre heures qui
  suivent l'heure choisie.
- **Heures calmes** (22 h 30 à 7 h 30 par défaut) et **Ne pas déranger** (le
  bouton sous l'orbe ou l'icône de notification, pour une heure), ainsi qu'une
  application en plein écran ou une présentation : rien n'est dit à voix
  haute. Un rappel que vous avez programmé donne quand même une notification
  et un petit son discret ; le reste, briefing compris, attend derrière la
  pastille de l'orbe.

---

## 🤝 Coexistence avec A.R.E.S

JARVIS est **la voix** (conversation, actions sur le PC, tâches Claude) ;
A.R.E.S est **l'intendant** (agenda, tâches, notes, rappels datés). JARVIS
consulte A.R.E.S en direct. Les deux s'entendent bien à condition de ne pas
répondre ensemble :

1. **A.R.E.S répond aussi à « Hey Jarvis »** (son mot d'éveil par défaut,
   `hey_jarvis`), et JARVIS aussi. Dans A.R.E.S, **laissez le mode
   mains-libres désactivé** (le bouton 👂), ou passez son mot d'éveil au
   moteur **Vosk** avec le mot **« Arès »** (A.R.E.S › Réglages › Application
   de bureau › Voix temps réel « Parler à A.R.E.S »).
2. **Ne gardez jamais deux conversations vocales ouvertes** en même temps (une
   dans A.R.E.S, une dans JARVIS) : elles s'entendraient l'une l'autre et
   coûteraient double.
3. **Activez le serveur MCP local d'A.R.E.S** : A.R.E.S › Réglages ›
   Application de bureau › « Serveur MCP local », port **6178** (celui par
   défaut). JARVIS le trouve tout seul, sans redémarrage : la pastille
   « A.R.E.S » passe à « joignable » et l'agenda apparaît dans le panneau.
   Réglages › Système › A.R.E.S le règle sur Automatique (s'il répond),
   Toujours ou Jamais.
4. **Facultatif, pour les tâches Claude** : JARVIS n'en a pas besoin pour
   l'agenda à la voix, mais une tâche Claude avec accès complet peut aussi se
   servir d'A.R.E.S si vous l'ajoutez à Claude Code. Tapez dans un terminal,
   **exactement** :
   ```
   claude mcp add --transport http --scope user ares http://127.0.0.1:6178/mcp
   ```
   Gardez le nom `ares` : c'est sous ce nom que JARVIS interdit aux tâches
   l'outil `remember` d'A.R.E.S, qui écrit dans les consignes d'A.R.E.S
   lui-même. (Si vous en choisissez un autre, mettez-le dans
   `JARVIS_ARES_MCP_NAME`.) `claude mcp list` doit afficher `ares` connecté.

À savoir :

- Ce serveur n'a **pas de mot de passe** : il n'écoute que votre PC
  (127.0.0.1), mais tout programme de votre PC peut le joindre. Si une future
  version d'A.R.E.S propose un jeton MCP, activez-le et mettez-le dans
  `JARVIS_ARES_TOKEN` (fichier `.env`).
- Le contenu des notes A.R.E.S est traité comme un texte extérieur : après en
  avoir lu une, JARVIS redemande votre accord avant d'écrire dans A.R.E.S
  (voir « Sécurité »).
- A.R.E.S utilise les raccourcis Ctrl+Alt+V, M, J, K et Espace ; JARVIS prend
  **Ctrl+Alt+Maj+J** pour ne pas les gêner.

---

## 🔒 Sécurité — à lire

### Ce que Claude a le droit de faire

Chaque tâche reçoit l'un de trois profils, une **liste blanche** d'outils :
ce qui n'y figure pas est refusé. JARVIS choisit le plus restreint qui suffit
et l'affiche sur la carte de la tâche ; sans profil précis, c'est **Lecture
seule**.

| Profil | Peut | Ne peut pas |
|---|---|---|
| **Web uniquement** (`recherche`) | Chercher et lire sur internet | Lire vos fichiers, lancer des commandes, écrire, utiliser vos connecteurs |
| **Lecture seule** (`lecture`, par défaut) | Lire et analyser vos fichiers | Modifier quoi que ce soit, lancer des commandes, aller sur internet, utiliser vos connecteurs, lire le `.env` ou les données de JARVIS |
| **Accès complet** (`complet`) | Créer et modifier des fichiers, lancer des commandes, internet, connecteurs | Avec ses outils de fichiers : modifier le dossier de JARVIS, ses données ou la configuration de Claude Code (`~/.claude`), lire le `.env` ; ni utiliser l'outil `remember` d'A.R.E.S (voir aussi plus bas) |

- Une page web piégée lue pendant une recherche ne peut donc ni fouiller vos
  fichiers ni lancer de commande. Ce que JARVIS sait de vous (mémoire, agenda,
  rappels) n'est jamais mis dans une tâche qui va sur internet, et une
  recherche web ne reprend jamais une session qui a vu vos fichiers.
- Les tâches Web uniquement et Lecture seule démarrent aussi sans hooks, sans
  plugins et sans fichiers CLAUDE.md (si votre version de Claude Code le
  permet, sinon le bilan de santé le signale). Au démarrage de chaque tâche,
  JARVIS vérifie la liste d'outils que Claude Code annonce : s'il y en a un de
  trop, la tâche est arrêtée (« Profil de sécurité non appliqué… »).

### Accès complet = votre confirmation

- Avant chaque tâche ou routine avec accès complet, JARVIS affiche la carte
  **« Confirmation requise »** avec la consigne entière, et vous demande
  « oui » à voix haute ou un clic sur **Lancer**. Sans réponse, la demande
  expire au bout de 90 secondes et rien n'est lancé.
- Un « oui » ne compte que s'il a été dit **après** la question : le modèle
  vocal ne peut pas se confirmer tout seul. « Réessayer » une tâche avec accès
  complet redemande aussi votre accord.
- Le champ texte (`/tâche …`) ne lance que des tâches Lecture seule.

### Après un contenu extérieur

Une page web, le résultat d'une tâche, les actualités, une note A.R.E.S, le
journal, votre écran ou le presse-papiers peuvent contenir des instructions
piégées. JARVIS les transmet au modèle vocal comme des **données**, jamais
comme des consignes. Et dès qu'un tel contenu est entré dans la conversation,
il redemande votre accord avant d'**ouvrir un site inconnu**, d'**écrire dans
le presse-papiers** ou d'**écrire dans A.R.E.S**. Le **verrouillage** du PC
passe toujours par un compte à rebours de 3 secondes (Échap pour annuler).

### Le mode `auto`

Les tâches tournent sans personne pour répondre à leurs questions : ce qui
demanderait une autorisation est **refusé** sur-le-champ (la tâche continue
sans, elle ne reste pas en attente). Le mode `auto` laisse passer ce qui est
sûr et refuse le risqué.

- Si Claude a été bloqué, la carte de la tâche dit « Claude demande
  l'autorisation d'utiliser … » avec **Autoriser** et **Refuser**. Autoriser
  reprend la même session avec cet outil ; les interdictions de JARVIS
  (tableau ci-dessus) restent en vigueur.
- Réglages › Claude Code propose aussi `acceptEdits` (fichiers autorisés,
  commandes refusées), `dontAsk` (seul ce qui est autorisé d'avance passe) et
  `bypassPermissions` (aucun contrôle) : ce dernier est **déconseillé**,
  affiché en rouge, et le bilan de santé le signale.

### Ce que les garde-fous ne couvrent pas

- En **accès complet**, un **script** ou une commande que Claude écrit puis
  exécute agit avec **vos** droits, partout où vous en avez : les
  interdictions ci-dessus ne valent que pour les outils de fichiers de
  Claude. Ce sont des garde-fous contre les erreurs et les pièges, pas une
  cage. C'est pour cela que la confirmation existe : lisez la consigne avant
  de dire « oui ».
- Le dossier de travail `~/JARVIS-travail` (`C:\Users\<vous>\JARVIS-travail`)
  est l'endroit où les tâches démarrent et rangent leur travail, **pas une
  barrière** : une tâche Lecture seule lit vos fichiers là où ils sont.
- Les connecteurs que vous ajoutez à Claude Code (mail, agenda, domotique…)
  donnent leurs pouvoirs aux tâches avec accès complet.
- Une page web peut mentir : un résumé de recherche reste à lire avec un œil
  critique.
- Ces protections demandent Claude Code 2.1.259 ou plus récent : tenez-le à
  jour (`claude update`).

### Vos secrets et votre PC

- Votre clé OpenAI **reste sur votre PC**, dans le fichier `.env` : seul
  JARVIS l'envoie à OpenAI. La page ne reçoit qu'une clé temporaire de deux
  minutes, et **les tâches Claude ne la reçoivent jamais** (elle est retirée
  de leur environnement, et la lecture du `.env` leur est interdite). Ne
  partagez jamais votre fichier `.env`.
- JARVIS n'accepte que les requêtes de **sa propre page, sur ce PC**
  (vérification de l'hôte et de l'origine, plus un jeton secret recréé à
  chaque démarrage). Ne mettez **jamais** ce serveur sur internet.
- Les réglages sensibles ne se changent que dans Réglages, avec
  confirmation, jamais à la voix.
- Plafond par tâche (2 $), durée maximale (10 min), trois tâches à la fois
  (Réglages › Claude Code) et plafond par jour (Réglages › Coûts).

---

## 🔁 Ce qui part où

| Quoi | Où ça va |
|---|---|
| Votre voix et ce que vous écrivez pendant une conversation | OpenAI |
| Au début de chaque conversation : ce que JARVIS sait de vous (mémoire), vos prochains rappels, l'agenda A.R.E.S du moment, et les derniers échanges après une reconnexion | OpenAI, pour que JARVIS vous réponde en connaissance de cause |
| Ce que JARVIS vous lit en conversation (résultat d'une tâche, notes A.R.E.S, journal, briefing, météo) | OpenAI |
| Captures d'écran et caméra | OpenAI, seulement quand vous le demandez |
| Votre voix en veille (mot d'éveil) | Rien ne sort avec l'écoute locale de Chrome ; sinon Google (Chrome) ou Microsoft (Edge) |
| Les annonces hors conversation, lues par la voix du navigateur | Les voix « Google » de Chrome et « Online (Natural) » d'Edge passent par leurs serveurs ; une voix Windows installée reste sur le PC |
| Les tâches : votre consigne, ce que Claude lit et, sauf en Web uniquement, votre mémoire | Anthropic, par Claude Code ; une recherche visite aussi les sites web |
| La météo | Open-Meteo reçoit le nom de votre ville et ses coordonnées, rien d'autre |
| Les actualités | Les flux RSS choisis (Le Monde par défaut) ; s'ils sont illisibles au briefing, une tâche Claude Web uniquement avec la seule date |
| L'agenda, les tâches et les notes A.R.E.S | Restent sur ce PC, entre JARVIS et A.R.E.S (127.0.0.1:6178) |
| Les polices et bibliothèques de la page | Google Fonts et jsDelivr, téléchargées comme pour n'importe quel site |
| Journal, mémoire, rappels, coûts, réglages | Ce PC, dossier `data` (Réglages › Données › Ouvrir le dossier data) |

---

## 📅 Échéances OpenAI

- **20 janvier 2027** : OpenAI arrête **gpt-realtime** et
  **gpt-realtime-mini**. JARVIS utilise déjà **gpt-realtime-2.1** par défaut.
  Si vous aviez choisi un ancien modèle (Réglages › Voix l'indique « arrêté
  le 20 janvier 2027 »), passez à gpt-realtime-2.1, ou à
  **gpt-realtime-2.1-mini**, moins cher.
- **26 février 2027** : OpenAI arrête **whisper-1**, **gpt-4o-transcribe** et
  **gpt-4o-mini-transcribe**. Ce sont eux qui écrivent ce que vous dites (sous
  l'orbe, dans le Journal, et pour reprendre le fil après une reconnexion).
  JARVIS utilise gpt-4o-mini-transcribe, avec whisper-1 en secours : tous deux
  sont concernés. La voix continuera de marcher, mais vos phrases pourraient
  ne plus s'afficher.

  Les remplaçants annoncés par OpenAI (gpt-transcribe, gpt-live-transcribe)
  ne sont pas encore confirmés pour le type de conversation qu'utilise JARVIS.
  **À tester avant le 26 février 2027** : dans le fichier `.env`, ajoutez
  `JARVIS_TRANSCRIBE_MODEL=gpt-realtime-whisper` (puis, si cela ne va pas,
  `gpt-transcribe`), relancez JARVIS et parlez-lui. Vérifiez que vos phrases
  s'affichent sous l'orbe et dans le Journal, sans carte d'erreur. Si OpenAI
  refuse le modèle, JARVIS repasse tout seul sur le modèle de secours ;
  Réglages › À propos › Transcription montre celui qui sert vraiment. Une mise
  à jour de JARVIS proposera le bon remplaçant avant cette date.
- Le bilan de santé (Réglages › Connexion) et Réglages › À propos vous
  rappellent ces dates pour les modèles que vous utilisez.

---

## 🔄 Mettre à jour

1. **Quittez JARVIS** : icône de notification → **Quitter JARVIS**.
2. **JARVIS lui-même** :
   - installé avec Git : dans le dossier de JARVIS, tapez `git pull` ;
   - installé avec le ZIP : renommez l'ancien dossier (par exemple
     `jarvis-ancien`), décompressez le nouveau ZIP à la même place et sous le
     même nom, puis recopiez-y votre fichier `.env` et votre dossier `data`
     (vos réglages, votre mémoire, vos rappels et votre journal).
3. **Ses dépendances**, dans le dossier de JARVIS :
   ```
   pip install -r requirements.txt
   ```
4. **Claude Code** :
   ```
   claude update
   ```
5. **Relancez JARVIS** (`JARVIS.bat`), puis Réglages › Connexion ›
   **Revérifier**. Réglages › À propos affiche la version de JARVIS (date et
   numéro, si vous l'avez installé avec Git) et celle de Claude Code.

Chrome et Edge se mettent à jour tout seuls.

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

`--scope user` rend le connecteur disponible partout, y compris dans le
dossier de travail des tâches. Les connecteurs de votre compte claude.ai
(Gmail, Google Agenda…) arrivent aussi dans les tâches, mais seulement si
Claude Code est connecté avec ce compte (pas avec une clé API) : le bilan de
santé vous le signale.

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

## 🔧 Réglages avancés (fichier `.env`)

Presque tout se règle dans **Réglages**. Le fichier `.env` (voir
`.env.example`, chaque ligne y est expliquée) donne les valeurs de départ ; ce
que vous changez dans Réglages l'emporte. Une ligne commençant par `#` est
ignorée. Les réglages sans section ne se changent que dans `.env`, avant de
relancer JARVIS.

| Variable | Défaut | Dans Réglages | Rôle |
|---|---|---|---|
| `OPENAI_API_KEY` | *(requis)* | Connexion | Clé API OpenAI pour la voix |
| `REALTIME_MODEL` | `gpt-realtime-2.1` | Voix | Modèle vocal (`gpt-realtime-2.1-mini` coûte moins cher) |
| `JARVIS_VOICE` | `ballad` | Voix | Voix (ballad = majordome ; alloy, ash, coral, echo, sage, shimmer, verse, marin, cedar) |
| `JARVIS_VOICE_SPEED` | `1.0` | Voix | Vitesse de la voix |
| `JARVIS_REASONING` | `low` | Voix | Effort de réflexion (minimal, low, medium, high, xhigh) |
| `JARVIS_WAKE_WORD` | `1` | Écoute | Mot d'éveil « Jarvis » actif au démarrage |
| `JARVIS_NOISE_REDUCTION` | `far_field` | Écoute | Micro du PC (`far_field`) ou casque (`near_field`) |
| `JARVIS_EAGERNESS` | `auto` | Écoute | Prise de parole (`low` si JARVIS vous coupe trop tôt) |
| `JARVIS_IDLE_MINUTES` | `3` | Écoute | Mise en veille après X minutes sans parler (0 = jamais) |
| `JARVIS_BRIEFING_TIME` | `08:00` | Proactivité | Heure du briefing du matin (vide = pas de briefing) |
| `JARVIS_BRIEFING_DAYS` | `lun-ven` | Proactivité | Jours du briefing (`tous`, `lun,mer,ven`…) |
| `JARVIS_BRIEFING_NEWS` | `0` | Proactivité | Ajouter les titres de l'actualité au briefing |
| `JARVIS_QUIET_HOURS` | `22:30-07:30` | Proactivité | Heures calmes : rien à voix haute |
| `JARVIS_CITY` | *(aucune)* | Proactivité | Votre ville, pour la météo |
| `JARVIS_REOPEN_ON_REMINDER` | `1` | Proactivité | Rouvrir la fenêtre quand un rappel arrive |
| `JARVIS_PERMISSION_MODE` | `auto` | Claude Code | Autorisations des tâches : `auto`, `acceptEdits`, `dontAsk`, `bypassPermissions` (déconseillé) |
| `JARVIS_WORKDIR` | `~/JARVIS-travail` | Claude Code | Dossier de travail des tâches |
| `JARVIS_MCP_CONFIG` | *(aucun)* | Claude Code | Fichier de connecteurs MCP en plus |
| `JARVIS_MODEL_SIMPLE` | `haiku` | Claude Code | Modèle Claude des tâches simples |
| `JARVIS_MODEL_NORMAL` | `sonnet` | Claude Code | Modèle Claude des tâches normales |
| `JARVIS_MODEL_COMPLEX` | `opus` | Claude Code | Modèle Claude des tâches complexes |
| `JARVIS_TASK_TIMEOUT` | `600` | Claude Code | Durée maximale d'une tâche, en secondes |
| `JARVIS_TASK_BUDGET_USD` | `2.0` | Claude Code | Plafond par tâche, en dollars (0 = aucun) |
| `JARVIS_MAX_CONCURRENT_TASKS` | `3` | Claude Code | Tâches Claude en même temps |
| `JARVIS_DAILY_BUDGET_USD` | `0` | Coûts | Plafond par jour, voix et tâches (0 = aucun) |
| `JARVIS_HOTKEY` | `ctrl+alt+shift+j` | Système | Raccourci global (Ctrl+Alt+Maj+J) |
| `JARVIS_BROWSER` | `auto` | Système | Navigateur de la fenêtre : `auto` (Chrome d'abord), `chrome` ou `edge` |
| `JARVIS_TRAY` | `1` | Système | Icône dans la zone de notification |
| `JARVIS_ARES` | `auto` | Système | A.R.E.S : `auto`, `on` ou `off` |
| `JARVIS_JOURNAL_DAYS` | `30` | Données | Jours gardés dans le journal (0 = pas de journal) |
| `JARVIS_LANGUAGE` | `français` | — | Langue parlée par JARVIS |
| `JARVIS_SPEECH_LANG` | *(selon la langue)* | — | Langue du mot d'éveil (`fr-FR` pour le français) |
| `JARVIS_PORT` | `8788` | — | Port du serveur local |
| `JARVIS_DATA_DIR` | `data` | — | Où JARVIS range sa mémoire, ses rappels, son journal, ses coûts et ses réglages |
| `JARVIS_TRANSCRIBE_MODEL` | `gpt-4o-mini-transcribe` | — | Modèle qui écrit ce que vous dites (voir « Échéances OpenAI ») |
| `JARVIS_TRANSCRIBE_FALLBACK` | `whisper-1` | — | Modèle de secours si OpenAI refuse le premier |
| `JARVIS_NEWS_FEEDS` | `https://www.lemonde.fr/rss/une.xml` | — | Flux RSS des actualités, séparés par des virgules |
| `JARVIS_OPEN_URL_ALLOW` | *(aucun)* | — | Sites ouverts sans confirmation, même après un contenu extérieur (ex. `youtube.com`) |
| `JARVIS_CONFIRM_COMPLET` | `1` | — | Confirmation avant une tâche avec accès complet (gardez `1`) |
| `JARVIS_PENDING_TTL` | `90` | — | Secondes pour confirmer avant que la demande expire |
| `JARVIS_SAFE_MODE` | `auto` | — | Tâches Web uniquement et Lecture seule sans hooks ni plugins (`auto`, `on`, `off`) |
| `JARVIS_RESTRICTED` | `auto` | — | Mode restreint de Claude Code pour les recherches web (`auto`, `on`, `off`) |
| `JARVIS_SECRET_TTL` | `120` | — | Durée de la clé temporaire donnée à la page, en secondes |
| `JARVIS_RETENTION_RATIO` | `0.8` | — | Part d'une longue conversation gardée quand elle devient trop longue |
| `JARVIS_ARES_URL` | `http://127.0.0.1:6178/mcp` | — | Adresse du serveur MCP d'A.R.E.S |
| `JARVIS_ARES_MCP_NAME` | `ares` | — | Nom d'A.R.E.S dans `claude mcp add` (voir « Coexistence avec A.R.E.S ») |
| `JARVIS_ARES_TOKEN` | *(aucun)* | — | Jeton envoyé à A.R.E.S, si une version future en demande un |

---

## ❓ Problèmes fréquents

Commencez par **Réglages › Connexion › Revérifier** : le bilan de santé dit ce
qui ne va pas et comment le corriger.

**« Clé OpenAI absente » ou « refusée »**
→ Collez votre clé dans Réglages › Connexion (ou vérifiez-la sur
platform.openai.com/api-keys).

**« Crédit OpenAI épuisé » (`insufficient_quota`, `429`)**
→ Ajoutez du crédit : étape 2, point 2.

**« Modèle vocal indisponible pour votre compte »**
→ Choisissez gpt-realtime-2.1 (ou gpt-realtime-2.1-mini) dans Réglages › Voix.

**« Claude Code est introuvable » sur les tâches**
→ Refaites l'étape 1 (`irm https://claude.ai/install.ps1 | iex` dans
PowerShell), ouvrez un nouveau terminal, puis vérifiez avec
`claude -p "test"`.

**« Claude Code n'est pas connecté »**
→ Dans un terminal, tapez `claude` (ou `claude auth login`) et connectez-vous.

**« Claude Code … est trop ancien » ou « Profil de sécurité non appliqué »**
→ Tapez `claude update`, puis Revérifier.

**Une tâche dit « Claude n'a pas eu le droit de… »**
→ C'est le mode `auto` qui a refusé une action. Cliquez sur **Autoriser** sur
sa carte si vous êtes d'accord, ou redemandez la tâche avec accès complet.

**« Plafond du jour atteint »**
→ Relevez le plafond dans Réglages › Coûts, ou attendez demain.

**Le micro ne marche pas**
→ Cadenas à gauche de l'adresse → Micro → Autoriser. Vérifiez aussi Paramètres
Windows › Confidentialité et sécurité › Microphone : « Accès au microphone »
et « Autoriser les applications de bureau à accéder au microphone ». Le bon
micro se choisit dans Réglages › Écoute.

**JARVIS n'entend pas « Jarvis »**
→ Le mot d'éveil a besoin de Chrome ou d'Edge, et du bouton **Mot d'éveil**
activé sous l'orbe. Le nom doit commencer la phrase. Le mot d'éveil écoute le
micro par défaut de Windows. En attendant, un clic sur l'orbe ou Espace marche
toujours.

**Deux voix répondent à « Jarvis »**
→ A.R.E.S écoute aussi : voir « Coexistence avec A.R.E.S ».

**« A.R.E.S injoignable »**
→ A.R.E.S doit être lancé ; dans A.R.E.S › Réglages › Application de bureau,
cochez « Serveur MCP local ».

**« Raccourci … indisponible (déjà utilisé) »**
→ Un autre programme l'a pris : choisissez-en un autre dans Réglages › Système.

**Pas d'icône JARVIS près de l'horloge, ou pas de capture d'écran**
→ Dans le dossier de JARVIS : `pip install -r requirements.txt`, puis
relancez JARVIS. Regardez aussi dans la flèche « ^ » des icônes cachées.

**Les graphiques ne s'affichent pas**
→ Ils se chargent en ligne : les chiffres restent dans le tableau en dessous.

**Python ou pip « n'est pas reconnu »**
→ Réinstallez Python en cochant « Add Python to PATH », ou utilisez
`py -m pip install -r requirements.txt`.

**Double-cliquer sur `JARVIS.bat` n'ouvre rien**
→ Lancez `python server.py --app` dans un terminal pour voir l'erreur, ou
lisez le journal `data\jarvis.log`.

**« Serveur JARVIS déconnecté »**
→ JARVIS a été fermé : relancez `JARVIS.bat`. La page se reconnecte toute
seule.

**« Le fichier … était abîmé »**
→ Un fichier de données était illisible : JARVIS en a gardé une copie et a
restauré la sauvegarde. Rien à faire.

---

## 🧪 Pour les développeurs

```
pip install -r requirements-dev.txt
python -m pytest                          # tests unitaires
python -m playwright install chromium     # une fois, pour les tests de bout en bout
python -m pytest -m e2e tests/e2e         # l'interface dans Chromium (une douzaine de minutes)
```

- Les tests remplacent Claude Code, OpenAI, WebRTC et la reconnaissance vocale
  par des imitations : ils ne coûtent rien, n'utilisent pas le réseau et ne
  touchent ni à votre PC, ni à votre `.env`, ni à votre dossier `data`.
  `JARVIS_E2E_CHROMIUM` indique un Chromium déjà installé.
- Syntaxe JavaScript (Git Bash) : `for f in static/js/*.js; do node --check "$f"; done`
- **Intégration continue** (`.github/workflows/tests.yml`, à chaque envoi sur
  `main` et à chaque pull request) : tests unitaires sous Windows
  (Python 3.12) et Ubuntu (Python 3.10), puis la syntaxe JavaScript ; les
  tests de bout en bout tournent sous Ubuntu, à titre indicatif.
- **Organisation** : `server.py` (FastAPI, en local seulement), `jarvis/` (un
  module par sujet : `tasks.py` et ses profils, `confirm.py`, `settings.py`,
  `health.py`, `scheduler.py`, `briefing.py`, `usage.py`, `ares.py`,
  `info.py`, `journal.py`, `shell.py` et `desktop.py` pour Windows),
  `static/js/` (modules ES ; tous les textes français sont dans
  `strings-fr.js`), `index.html`.
- **Règles de la maison** : toute nouvelle route `/api` s'ajoute à `KNOWN_API`
  dans `tests/test_security.py` (un balayage vérifie le jeton, l'hôte et
  l'origine sur chacune) ; un texte venu d'ailleurs s'affiche avec
  `textContent` ou `core.md`, jamais en HTML brut ; Python 3.10 compatible ;
  interface en français, avec « vous » ; aucun test ne dépend de l'heure ;
  jamais de `.env` ni de `data/` dans git.
- `tests/test_readme.py` vérifie ce README contre le code : sections,
  commandes, raccourcis, réglages et valeurs par défaut.

## 📄 Licence

MIT.
