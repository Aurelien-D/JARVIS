/* Every French string of the interface, in one place (design spec §11):
   "vous" everywhere, "tâche" is feminine. Parameterised strings are
   functions. The source is written with plain spaces; fr() sets the French
   typography once, at load (a narrow no-break space before ? ! : ; and
   inside « »), so the strings stay readable here and correct on screen. */

const NNBSP = "\u202f";  // espace fine insécable
const NBSP = "\u00a0";

/* French typography for one string: U+202F before ? ! : ; » and after «.
   Idempotent, and safe on URLs and clock times (no space before their colon). */
export function fr(s) {
  return String(s ?? "")
    .replace(/[ \u00a0]+([?!:;»])/g, `${NNBSP}$1`)
    .replace(/«[ \u00a0\u202f]*/g, `«${NNBSP}`)
    .replace(/([^\s\u202f])»/g, `$1${NNBSP}»`);
}

// Applied to the whole dictionary below: functions get their result typeset.
function typeset(node) {
  for (const [key, value] of Object.entries(node)) {
    if (typeof value === "string") node[key] = fr(value);
    else if (typeof value === "function") node[key] = (...args) => fr(value(...args));
    else if (value && typeof value === "object") typeset(value);
  }
  return node;
}

export const T = typeset({
  status: {
    off: "Hors ligne · cliquez sur l'orbe ou appuyez sur Espace pour parler",
    standby: "En veille · dites « Jarvis » ou cliquez sur l'orbe",
    standbyLocal: " · écoute locale",
    standbyCloud: " · écoute via Google",
    standbyElsewhere: " · écoute dans l'autre fenêtre",
    wakeOff: "En veille · mot d'éveil désactivé · cliquez sur l'orbe",
    connecting: "Connexion…",
    reconnecting: (n) => `Reconnexion (${n}/5)…`,
    listening: "Je vous écoute…",
    user: "Je vous entends…",
    thinking: "Réflexion…",
    speaking: "JARVIS répond · parlez ou appuyez sur Échap pour l'interrompre",
    confirm: "En attente de votre confirmation",
    muted: "Micro coupé · Ctrl+M pour le réactiver",
    countdown: (s) => `Veille dans ${s} s`,
    tasks: (n, elapsed) => (n === 1 ? ` · 1 tâche en cours (${elapsed})` : ` · ${n} tâches en cours`),
    toolElapsed: (s) => ` (${s} s)`,
    serverDown: "Serveur JARVIS déconnecté · reconnexion…",
    serverClosed: "JARVIS est fermé · relancez JARVIS.bat",
    error: "Erreur",
  },
  tool: {
    delegate_to_claude: "Je confie la tâche à Claude…",
    open_app: (a) => `Ouverture de ${(a && a.name) || "l'application"}…`,
    open_url: "Ouverture du lien…",
    system_control: "Commande système…",
    look_at_screen: "Je regarde l'écran…",
    look_at_camera: "Je regarde la caméra…",
    schedule: "Programmation…",
    remember: "Je retiens…",
    forget: "J'oublie…",
    get_status: "Je fais le point…",
    info: (a) => (a && /actu|news/i.test(a.type || a.kind || a.topic || "") ? "Je consulte l'actualité…" : "Je consulte la météo…"),
    infoWeather: "Je consulte la météo…",
    infoNews: "Je consulte l'actualité…",
    ares_lire: "Je consulte A.R.E.S…",
    ares_ajouter: "J'écris dans A.R.E.S…",
    ares_modifier: "J'écris dans A.R.E.S…",
    recall: "Je cherche dans le journal…",
    snooze_reminder: "Je reporte le rappel…",
    cancel_task: "J'annule la tâche…",
    cancel_schedule: "J'annule le rappel…",
    confirm_action: (a) => ((a && a.decision) === "non" ? "J'annule…" : "Je lance…"),
    display_card: "J'affiche…",
    display_report: "Je prépare le tableau de bord…",
    end_conversation: "Au revoir…",
  },
  controls: {
    micOn: "Micro : activé",
    micOff: "Micro : coupé",
    interrupt: "Interrompre",
    sleep: "Veille",
    wakeOn: "Mot d'éveil : activé",
    wakeOff: "Mot d'éveil : désactivé",
    lastReport: "Dernier rapport",
    clearAll: "Tout effacer",
    copy: "Copier",
    copied: "Copié !",
    panel: "Panneau",
    journal: "Journal",
    settings: "Réglages",
    help: "Aide",
    composerPlaceholder: "Écrivez à JARVIS… (Ctrl+J)",
    composerLabel: "Message pour JARVIS",
    send: "Envoyer",
  },
  // The HUD's own chrome: labels for buttons, regions and screen readers.
  hud: {
    skip: "Aller au champ de message",
    orbTalk: "Parler à JARVIS",
    orbSleep: "Mettre JARVIS en veille",
    cards: "Affichages de JARVIS",
    side: "Panneau latéral",
    actions: "Actions",
    liveControls: "Commandes de la conversation",
    closeCard: (title) => `Fermer la carte « ${title} »`,
    newCard: (title) => `Nouvelle carte : ${title}`,
    moreCards: (n) => `+${n}`,
    moreCardsLabel: (n) => (n === 1 ? "Afficher 1 autre carte" : `Afficher ${n} autres cartes`),
    fewerCards: "Réduire les cartes",
    wholeCard: "Afficher toute la carte",
    closePanel: "Fermer le panneau",
    copyCode: (title) => `Copier le code de « ${title} »`,
    copyFailed: "Copie impossible : sélectionnez le texte à la main.",
    dismissError: "Effacer le message d'erreur",
    closeReport: "Fermer le rapport",
    you: (text) => `Vous : ${text}`,
  },
  time: {
    justNow: "à l'instant",
    minutesAgo: (n) => `il y a ${n} min`,
    hoursAgo: (n) => `il y a ${n} h`,
    inMinutes: (n) => `dans ${n} min`,
    inHours: (n) => `dans ${n} h`,
    at: (time) => `à ${time}`,
    yesterday: (time) => `hier à ${time}`,
    tomorrow: (time) => `demain à ${time}`,
    onDate: (date, time) => `${date} à ${time}`,
  },
  empty: {
    tasks: "Aucune tâche. Dites par exemple « Jarvis, cherche les meilleurs aspirateurs robots sous 400 € ».",
    reminders: "Aucun rappel. « Rappelle-moi dans 10 minutes de sortir le pain. »",
    memory: "Je ne sais encore rien de vous. « Retiens que je préfère le thé. »",
    journal: "Rien dans le journal aujourd'hui.",
    agenda: "Rien de prévu aujourd'hui.",
    agendaDown: "A.R.E.S n'est pas joignable : activez son serveur MCP local (A.R.E.S › Réglages › Application de bureau).",
  },
  task: {
    status: { running: "En cours", done: "Terminée", cancelled: "Annulée", error: "Échec",
              attente: "En attente de confirmation", queued: "En file", en_file: "En file",
              interrompue: "Interrompue", interrupted: "Interrompue" },
    profile: { recherche: "Web uniquement", lecture: "Lecture seule", complet: "Accès complet" },
    complexity: { simple: "Simple", normale: "Normale", complexe: "Complexe" },
    actions: { read: "Lire", copy: "Copier", continue: "Continuer", retry: "Réessayer",
               cancel: "Annuler la tâche", reveal: "Afficher dans l'explorateur" },
    followUp: (title) => `suite de « ${title} »`,
    untitled: "Tâche",
    sectionTitle: (n) => `Sessions Claude Code · ${n} en cours`,
    history: (n) => `Historique (${n})`,
    working: "Claude Code travaille…",
    defaultModel: "modèle par défaut",
    routine: "Routine",
    briefing: "Briefing",
    cancelLabel: (title) => `Annuler la tâche « ${title} »`,
    outputLabel: (title) => `Résultat de la tâche « ${title} »`,
    deleteReminder: (text) => `Supprimer le rappel « ${text} »`,
    forget: (text) => `Oublier « ${text} »`,
  },
  error: {
    noKey: "Clé OpenAI absente : ajoutez-la dans Réglages › Connexion, puis réessayez.",
    unauthorized: "Clé OpenAI refusée : vérifiez-la sur platform.openai.com/api-keys.",
    quota: "Crédit OpenAI épuisé : vérifiez la facturation sur platform.openai.com.",
    rate: "Trop de demandes envoyées à OpenAI : réessayez dans un instant.",
    model: "Modèle vocal indisponible pour votre compte : choisissez-en un autre dans Réglages › Voix.",
    network: "Pas de connexion à OpenAI : vérifiez internet.",
    timeout: "OpenAI ne répond pas (20 s) : réessayez.",
    server: "Serveur JARVIS injoignable : relancez JARVIS.bat.",
    NotAllowedError: "Micro bloqué : cliquez sur le cadenas de la barre d'adresse › Microphone › Autoriser. Vérifiez aussi Windows › Confidentialité › Microphone › « Autoriser les applications de bureau ».",
    NotFoundError: "Aucun micro détecté : branchez-en un ou choisissez-le dans Réglages › Écoute.",
    NotReadableError: "Le micro est occupé par une autre application ou bloqué par Windows (Confidentialité › Microphone).",
    lost: "Connexion perdue. Vérifiez internet, puis cliquez sur l'orbe.",
    wakeRefused: "Micro ou reconnaissance vocale refusés : mot d'éveil en pause. Cliquez sur l'orbe pour parler.",
    inaudible: "Je vous entends mal : rapprochez-vous du micro ou écrivez dans le champ texte.",
    contentFilter: "Réponse interrompue par le filtre de sécurité d'OpenAI.",
    failed: (reason) => `Réponse interrompue : ${reason}.`,
    image: "Image trop lourde pour être envoyée.",
    claudeMissing: "Claude Code est introuvable. Installez-le : ouvrez PowerShell et tapez irm https://claude.ai/install.ps1 | iex",
    claudeLoggedOut: "Claude Code n'est pas connecté : ouvrez un terminal, tapez claude et connectez-vous.",
    sandbox: "Profil de sécurité non appliqué par cette version de Claude Code : tâche arrêtée. Mettez Claude Code à jour (claude update).",
    budget: (amount) => `Plafond du jour atteint (${amount}). Modifiable dans Réglages › Coûts.`,
    hotkey: (combo) => `Raccourci ${combo} indisponible (déjà utilisé). Choisissez-en un autre dans Réglages › Système.`,
    corruptFile: (name) => `Le fichier ${name} était abîmé : une copie a été gardée et la sauvegarde restaurée.`,
    unknown: "Une erreur inattendue s'est produite : réessayez.",
    retry: "Réessayer",
  },
  confirm: {
    title: "Confirmation requise",
    complet: (title) => `Confier à Claude, avec accès complet à vos fichiers et commandes : « ${title} ».`,
    link: (domain) => `Ouvrir ${domain} ?`,
    clipboard: "Remplacer le contenu du presse-papiers ?",
    completRoutine: (freq, title) => `Programmer une routine ${freq} avec accès complet : « ${title} ».`,
    taskApproval: (tool, title) => `Claude demande l'autorisation d'utiliser ${tool} pour « ${title} ».`,
    run: "Lancer",
    cancel: "Annuler",
    expiresIn: (s) => `Expire dans ${s} s`,
    expired: "Demande expirée : rien n'a été lancé.",
    done: "Lancé.",
    cancelled: "Annulé, rien n'a été fait.",
    failed: (reason) => `Échec : ${reason}`,
    lock: (s) => `Verrouillage dans ${s} s · Échap pour annuler`,
    lockTitle: "Verrouillage",
    lockBody: "Le PC va se verrouiller.",
    lockCancelled: "Verrouillage annulé.",
    lockCancel: "Annuler le verrouillage",
    dismiss: "Refuser la demande et fermer la carte",
    // Who may launch a request (spec 4.11): the other device sees [Annuler] and this note.
    fromPc: "Demandée sur le PC : elle se lance sur le PC.",
    fromPhone: "Demandée depuis l'iPhone : elle se lance sur l'iPhone.",
    // open_url from the phone: a card with the link, nothing opens on the PC.
    linkTitle: "Lien à ouvrir",
    openLink: "Ouvrir le lien",
    linkTainted: "Lien proposé après des données externes : vérifiez-le.",
  },
  delivery: {
    badge: (n) => (n > 1 ? `${n} messages en attente · cliquez pour les écouter`
                         : `${n} message en attente · cliquez pour l'écouter`),
    missed: "Pendant votre absence : …",
    dnd: (time) => `Ne pas déranger jusqu'à ${time}`,
    snooze10: "+10 min",
    snooze60: "+1 h",
    tomorrow: "Demain",
    done: "Fait",
    reminderDeleted: "Rappel supprimé · Annuler",
    factForgotten: "Souvenir oublié · Annuler",
    notifAsk: "Voulez-vous une notification Windows quand une tâche se termine ou qu'un rappel arrive ?",
    notifEnable: "Activer",
    notifLater: "Plus tard",
    otherPage: "JARVIS est actif dans une autre fenêtre.",
    otherLive: "JARVIS est en conversation dans une autre fenêtre : celle-ci prendra le relais dès que l'autre sera en veille.",
    useThisPage: "Utiliser celle-ci",
    otherTitle: "Autre fenêtre",
    dndHour: "Ne pas déranger 1 h",
    dndEnd: "Arrêter « Ne pas déranger »",
    quietHours: "Heures calmes",
    quietEdit: "Modifier dans Réglages › Proactivité",
    dndFailed: "« Ne pas déranger » n'a pas pu être enregistré : réessayez.",
    notifTitle: "Notifications",
    warning: "Attention",
    reminder: "Rappel",
    // 45 → « 45 min », 135 → « 2 h 15 », 2880 → « 2 jours » (a reminder missed over a weekend)
    lateBy: (minutes) => {
      const m = Math.max(1, Math.round(Number(minutes) || 0));
      if (m < 60) return `${m} min`;
      if (m < 1440) return m % 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}` : `${m / 60} h`;
      const d = Math.round(m / 1440);
      return `${d} jour${d > 1 ? "s" : ""}`;
    },
    reminderLate: (minutes) => ` (en retard de ${T.delivery.lateBy(minutes)})`,
    reminderAt: (time) => `Rappel (${time})`,
    reminderDueAt: (time, minutes) => `Rappel de ${time} (en retard de ${T.delivery.lateBy(minutes)})`,
    // WP17: the buttons of a reminder that went off, and the local briefing's card
    snoozed: (time) => `Rappel reporté : ${time}.`,
    snoozeFailed: (why) => `Report impossible : ${why}`,
    briefing: "Briefing du matin",
    // The paired iPhone when JARVIS stops on the PC (sse.serverClosed).
    pcClosed: "JARVIS est fermé sur le PC",
  },
  // ---- WP10 + WP11 (panels.js, taskview.js, report.js) -------------------
  // The report window (report.js): its table, chart and ApexCharts' toolbar.
  report: {
    untitled: "Rapport",
    column: (n) => `Colonne ${n}`,
    tableLabel: "Tableau du rapport",
    copyCsv: "Copier CSV",
    copyCsvLabel: "Copier le tableau au format CSV",
    previous: "Précédent",
    next: "Suivant",
    rows: (from, to, n) => `Lignes ${from} à ${to} sur ${n}`,
    yes: "oui",
    no: "non",
    chartLoading: "Chargement du graphique…",
    chartUnavailable: "Graphique indisponible : la bibliothèque n'a pas pu être chargée (connexion à internet ?). Les chiffres sont dans le tableau.",
    chartFailed: "Ce graphique n'a pas pu être dessiné. Les chiffres sont dans le tableau.",
    chartLabel: (names) => (names ? `Graphique : ${names}` : "Graphique"),
    toolbar: { exportToSVG: "Télécharger en SVG", exportToPNG: "Télécharger en PNG", exportToCSV: "Télécharger en CSV",
               menu: "Menu", selection: "Sélection", selectionZoom: "Zoom sur la sélection", zoomIn: "Zoomer",
               zoomOut: "Dézoomer", pan: "Déplacer", reset: "Réinitialiser le zoom", measure: "Mesurer" },
  },
  // The side panel (panels.js): sections, task cards, reminders, memory.
  panels: {
    tasks: "Sessions Claude Code",
    reminders: "Rappels & routines",
    memory: "Mémoire",
    count: (title, n) => `${title} (${n})`,
    groups: { today: "Aujourd'hui", tomorrow: "Demain", week: "Cette semaine", later: "Plus tard" },
    readLabel: (title) => `Lire la tâche « ${title} »`,
    copyLabel: (title) => `Copier le résultat de « ${title} »`,
    continueLabel: (title) => `Continuer la tâche « ${title} »`,
    retryLabel: (title) => `Réessayer la tâche « ${title} »`,
    continueText: (title) => `Suite de « ${title} » : `,
    launch: "Lancer",
    launchLabel: (title) => `Lancer la tâche « ${title} »`,
    dismiss: "Annuler",
    dismissLabel: (title) => `Annuler la demande « ${title} »`,
    approvalAsk: (tools) => `Claude demande l'autorisation d'utiliser ${tools}.`,
    approve: "Autoriser",
    approveLabel: (title) => `Autoriser et reprendre la tâche « ${title} »`,
    refuse: "Refuser",
    refuseLabel: (title) => `Refuser l'autorisation pour « ${title} »`,
    retried: (title) => `Nouvel essai de « ${title} » lancé.`,
    retryConfirm: "Accès complet : confirmez le nouvel essai sur la carte « Confirmation requise ».",
    retryFailed: (why) => `Nouvel essai impossible : ${why}`,
    decideFailed: (why) => `Décision non enregistrée : ${why}`,
    model: (name) => `Modèle : ${name}`,
    origins: { routine: "Routine", briefing: "Briefing", approbation: "reprise autorisée" },
    queued: "En attente d'une place libre",
    reminderDeleted: "Rappel supprimé",
    factForgotten: "Souvenir oublié",
    undo: "Annuler",
    deleteFailed: (why) => `Suppression impossible : ${why}`,
    edit: "Modifier",
    editReminder: (text) => `Modifier le rappel « ${text} »`,
    editFact: (text) => `Modifier « ${text} »`,
    editField: "Nouveau texte",
    save: "Enregistrer",
    cancelEdit: "Annuler la modification",
    saveFailed: (why) => `Modification impossible : ${why}`,
    factDate: (date) => `Retenu le ${date}`,
    // WP17: a reminder's date and time in the edit form, and the new repeats
    editWhen: "Date et heure",
    repeats: { daily: "chaque jour", weekdays: "en semaine", weekly: "chaque semaine", monthly: "chaque mois" },
    weekdays: ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],  // the server's 0 = lundi
    repeatDays: (days) => `le ${days}`,
  },
  // The task viewer (taskview.js): a task's whole story.
  taskview: {
    close: "Fermer la fiche de la tâche",
    facts: "Détails",
    status: "Statut",
    profile: "Profil",
    model: "Modèle",
    complexity: "Complexité",
    duration: "Durée",
    steps: "Étapes",
    cost: "Coût estimé",
    costValue: (amount) => `${amount} (estimation, équivalent API)`,
    started: "Démarrée",
    origin: "Origine",
    origins: { voix: "à la voix", clavier: "au clavier", routine: "routine", briefing: "briefing",
               approbation: "reprise autorisée" },
    followUp: "Suite de",
    prompt: "Demande",
    note: "Note",
    output: "Résultat",
    noOutput: "Pas encore de résultat.",
    files: "Fichiers créés ou modifiés",
    reveal: "Afficher dans l'explorateur",
    revealLabel: (name) => `Afficher « ${name} » dans l'explorateur`,
    revealUnavailable: "L'explorateur ne peut pas être ouvert d'ici sur ce système.",
    revealFailed: (why) => `Impossible d'afficher le fichier : ${why}`,
    denials: "Refus",
    denialsIntro: "Claude n'a pas eu le droit de :",
    log: "Étapes suivies",
    logLoading: "Chargement des étapes…",
    logEmpty: "Aucune étape enregistrée.",
    logFailed: "Étapes indisponibles pour le moment.",
    copyOutput: "Copier le résultat",
    seconds: (s) => `${s} s`,
    minutes: (m, s) => `${m} min ${s} s`,
    hours: (h, m) => `${h} h ${m} min`,
  },
  // ---- end of WP10 + WP11 -------------------------------------------------
  // Cards of the voice session (voice.js).
  voice: {
    lostTitle: "Connexion perdue",
    sessionTitle: "Session vocale",
    openaiProblem: (what) => `OpenAI signale un problème : ${what}`,
    unknownError: "erreur inconnue",
    responseTitle: "Réponse interrompue",
    launchTitle: "Lancement",
    opening: (name, monitor) => `Ouverture de **${name}**${monitor ? ` → écran **${monitor}**` : ""}…`,
    screen: "Écran",
    camera: "Caméra",
  },
  wake: {
    title: "Mot d'éveil",
    local: "écoute locale",
    cloud: "écoute via Google",
    paused: "en pause (micro refusé)",
    elsewhere: "dans l'autre fenêtre",
    denied: "Micro refusé dans le navigateur : mot d'éveil désactivé. Autorisez le micro (cadenas de la barre d'adresse › Microphone), puis réactivez le mot d'éveil.",
    installed: "Pack vocal français hors ligne installé.",
    capped: "Plafond de dépenses du jour atteint : le mot d'éveil n'ouvre plus de conversation. Cliquez sur l'orbe si besoin.",
  },
  composer: {
    helpTitle: "Ce que je sais faire",
    helpButton: "? Aide",
    helpButtonTitle: "Ce que je sais faire (touche ?)",
    chips: "Suggestions",
    shortcuts: "Raccourcis clavier",
    continueTask: "Continuer la tâche",
    asTable: "Afficher en tableau",
    thanks: "Merci, c'est tout",
    taskSent: (text) => `Tâche confiée à Claude : « ${text} »`,
    taskEmpty: "Écrivez la tâche après /tâche, par exemple : /tâche compare trois aspirateurs robots",
    taskFailed: (why) => `Tâche non lancée : ${why}`,
  },
  onboarding: {
    title: "Mise en route",
    steps: ["Clé OpenAI", "Claude Code", "Micro", "Votre micro est…", "Mot d'éveil", "A.R.E.S", "Notifications"],
    micKinds: { headset: "Un casque", builtin: "Le micro du PC ou de la webcam" },
    states: { ok: "OK", fix: "À corriger", info: "Info" },
    ares: "A.R.E.S répond aussi à « Hey Jarvis ». Pour éviter deux voix, laissez son mode mains-libres désactivé ou choisissez le moteur Vosk avec le mot « Arès ».",
    buttons: { recheck: "Revérifier", testMic: "Tester le micro", testVoice: "Tester la voix", finish: "Terminer" },
    // ---- the Mise en route dialog (onboarding.js, WP12)
    intro: "Quelques vérifications avant de commencer. Ce qui est marqué « À corriger » empêche JARVIS de fonctionner normalement ; le reste est pour information.",
    close: "Fermer la mise en route",
    checking: "Vérification…",
    unchecked: "Non vérifié.",
    stepsLabel: "Étapes de la mise en route",
    others: "Autres vérifications",
    serverDown: "Le serveur JARVIS ne répond pas : relancez JARVIS.bat, puis cliquez sur Revérifier.",
    key: {
      label: "Clé OpenAI",
      input: "Nouvelle clé OpenAI",
      save: "Enregistrer la clé",
      replace: "Remplacer la clé",
      add: "Ajouter la clé",
      cancel: "Annuler",
      saved: "Clé enregistrée.",
      none: "Aucune clé enregistrée.",
      current: (masked) => `Clé enregistrée : ${masked}`,
      help: "Créez-la sur platform.openai.com/api-keys. Elle reste sur ce PC (fichier .env) et n'est jamais réaffichée en entier.",
    },
    mic: {
      granted: (n) => (n > 1 ? `Micro autorisé · ${n} micros détectés.` : "Micro autorisé."),
      prompt: "Le navigateur vous demandera l'autorisation au premier usage : testez-le maintenant.",
      unknown: "Autorisation du micro inconnue : testez-le.",
      meter: "Niveau du micro",
      listening: "Parlez : la barre doit bouger…",
      works: "Le micro fonctionne.",
      silent: "Je n'entends rien : vérifiez le micro choisi (Réglages › Écoute) et qu'il n'est pas coupé.",
      unsupported: "Ce navigateur ne donne pas accès au micro.",
    },
    micKind: "Pour régler la réduction de bruit des conversations.",
    micKindSaved: "Enregistré · appliqué à la prochaine conversation.",
    wake: {
      none: "Ce navigateur n'a pas de reconnaissance vocale : cliquez sur l'orbe ou appuyez sur Espace pour parler.",
      off: "Mot d'éveil désactivé : cliquez sur l'orbe ou appuyez sur Espace pour parler.",
      local: "Écoute locale : en veille, rien ne quitte ce PC.",
      cloud: "Écoute via Google : en veille, le son passe par les serveurs de Google. Le pack français hors ligne s'installe au premier clic sur l'orbe, quand Chrome le propose.",
      reader: "Avec un lecteur d'écran, sa voix peut déclencher le mot d'éveil ou couper JARVIS : désactivez le mot d'éveil et écrivez dans le champ texte (Ctrl+J), ou activez « Maintenir Espace pour parler » (Réglages › Écoute).",
    },
    notif: {
      granted: "Notifications autorisées : une tâche finie ou un rappel vous est signalé même JARVIS en arrière-plan.",
      default: "Une notification Windows peut vous signaler une tâche finie ou un rappel.",
      denied: "Notifications refusées dans le navigateur (cadenas de la barre d'adresse › Notifications).",
      unsupported: "Ce navigateur n'affiche pas de notifications.",
      enable: "Activer",
    },
    voice: {
      sample: "Bonjour monsieur. Si vous m'entendez clairement, le son fonctionne.",
      note: "Voix du navigateur, utilisée pour les annonces hors conversation.",
      none: "Ce navigateur n'a pas de voix de synthèse.",
    },
    finishFailed: "La mise en route n'a pas pu être enregistrée : réessayez.",
  },
  settings: {
    sections: ["Voix", "Écoute", "Proactivité", "Claude Code", "Coûts", "Système", "Données", "À propos"],
    eagernessLow: "Il me coupe trop tôt",
    sensitive: "Réglage sensible : il ne peut être modifié qu'ici, jamais à la voix.",
    bypass: "Mode sans garde-fou : Claude peut tout modifier sans contrôle. Déconseillé.",
    data: "Ce qui part où : votre voix pendant une conversation → OpenAI ; en veille → reconnaissance locale de Chrome, ou Google si elle n'est pas disponible ; captures d'écran et caméra → OpenAI, seulement quand vous le demandez ; tâches → Claude (Anthropic) ; journal, mémoire et rappels → ce PC (dossier data).",
    purgeJournal: "Purger le journal",
    openData: "Ouvrir le dossier data",
    // ---- the Réglages dialog (settings.js, WP12)
    title: "Réglages",
    close: "Fermer les réglages",
    nav: "Sections des réglages",
    loading: "Chargement des réglages…",
    loadFailed: "Les réglages n'ont pas pu être chargés : vérifiez que JARVIS tourne, puis rouvrez cette fenêtre.",
    when: {
      now: "Enregistré.",
      session: "Enregistré · appliqué à la prochaine conversation.",
      task: "Enregistré · appliqué à la prochaine tâche.",
      restart: "Enregistré · redémarrage nécessaire.",
    },
    restart: (names) => `Redémarrage nécessaire pour : ${names}. Quittez JARVIS, puis relancez JARVIS.bat.`,
    sensitiveTag: " (réglage sensible)",
    confirmTitle: "Réglage sensible",
    confirmValue: (label, value) => `${label} : ${value}`,
    confirmOk: "Confirmer la modification",
    apply: "Appliquer",
    notApplied: "Pas encore appliqué : choisissez « Appliquer ».",
    keyReplace: (old) => `La clé OpenAI enregistrée (${old}) sera remplacée.`,
    cancel: "Annuler",
    empty: "(vide)",
    current: (value) => `${value} (valeur actuelle)`,
    voiceNote: "Les changements de voix et de modèle s'appliquent à la prochaine conversation.",
    healthTitle: "Bilan de santé",
    recheck: "Revérifier",
    openOnboarding: "Ouvrir la mise en route",
    checking: "Vérification…",
    healthFailed: "Le bilan de santé n'a pas pu être fait : le serveur JARVIS ne répond pas.",
    keyAbove: "ci-dessus",  // the key's fix in Réglages › Connexion, where the key form is
    hours: { on: "Activées", from: "De", to: "à" },
    time: { on: "Activé" },
    days: { lun: "Lun", mar: "Mar", mer: "Mer", jeu: "Jeu", ven: "Ven", sam: "Sam", dim: "Dim" },
    daysFull: { lun: "lundi", mar: "mardi", mer: "mercredi", jeu: "jeudi", ven: "vendredi", sam: "samedi", dim: "dimanche" },
    unit: { min: "min", "$": "$", jours: "jours", s: "s" },
    noSR: "Ce navigateur n'a pas de reconnaissance vocale : le mot d'éveil n'y est pas disponible.",
    devices: {
      mic: "Micro des conversations",
      speaker: "Sortie audio",
      system: "Par défaut du système",
      micN: (n) => `Micro ${n}`,
      speakerN: (n) => `Sortie ${n}`,
      gone: "Appareil enregistré (débranché)",
      names: "Afficher les noms des appareils",
      wakeNote: "Le mot d'éveil écoute toujours le micro par défaut de Windows.",
      noSink: "Ce navigateur ne permet pas de choisir la sortie audio.",
      micSaved: "Enregistré · appliqué à la prochaine conversation.",
    },
    ptt: "Maintenir Espace pour parler",
    pttHelp: "En conversation, JARVIS ne vous écoute que tant que vous maintenez Espace (pratique avec un lecteur d'écran).",
    autostart: "Lancer JARVIS au démarrage de Windows",
    autostartNA: "Disponible sous Windows seulement.",
    motion: "Animations",
    motionAuto: "Selon Windows",
    motionReduced: "Réduites",
    motionHelp: "Réduites : l'orbe ne tourne plus et rien ne clignote.",
    volume: "Volume des sons",
    volumeValue: (n) => `${n} %`,
    dataFolder: "Dossier des données",
    purgeAsk: "Effacer tout le journal des conversations ? C'est définitif.",
    purgeOk: "Effacer le journal",
    purged: "Journal effacé.",
    purgeMissing: "Le journal n'est pas encore disponible dans cette version.",
    opened: "Dossier ouvert.",
    about: {
      jarvis: "JARVIS", claude: "Claude Code", python: "Python", voice: "Modèle vocal",
      transcribe: "Transcription", claudeModels: "Modèles Claude", browser: "Navigateur", wake: "Mot d'éveil",
      deadlines: "Échéances", update: "Mise à jour",
      updateText: "Pour mettre JARVIS à jour, suivez le README, section « Mettre à jour ».",
      checking: "vérification…", unknown: "inconnu",
      noDeadline: "Aucune pour les modèles choisis.",
      models: (m) => `simple : ${m.simple || "défaut"} · normale : ${m.normale || "défaut"} · complexe : ${m.complexe || "défaut"}`,
      wakeLocal: "écoute locale", wakeCloud: "écoute via Google", wakeNone: "indisponible dans ce navigateur",
    },
  },
  // The conversation journal (journal.js, WP14).
  // The weather and headlines cards (hud.js showInfo, WP16).
  info: {
    newsTitle: "Titres de l'actualité",
    weatherTitle: (city) => (city ? `Météo · ${city}` : "Météo"),
  },
  journal: {
    days: ["dimanche", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"],
    months: ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"],
    title: "Journal",
    close: "Fermer le journal",
    day: "Jour affiché",
    today: "Aujourd'hui",
    yesterday: "Hier",
    search: "Rechercher dans le journal",
    searchHint: "Rechercher…",
    you: "Vous",
    jarvis: "JARVIS",
    system: "Événement",
    log: "Échanges",
    emptyDay: "Rien dans le journal ce jour-là.",
    noMatch: (q) => `Aucun résultat pour « ${q} ».`,
    matches: (n, q) => `${n === 1 ? "1 résultat" : `${n} résultats`} pour « ${q} », sur tous les jours gardés`,
    keep: (n) => `Gardé ${n} jours sur ce PC.`,
    disabled: "Journal désactivé : rien n'est gardé sur ce PC (Réglages › Données).",
    failed: "Journal indisponible pour le moment : réessayez.",
    clear: "Effacer l'historique",
    clearAsk: "Effacer tout le journal de ce PC ? C'est définitif.",
    clearYes: "Oui, tout effacer",
    cancel: "Annuler",
    cleared: "Journal effacé.",
    clearFailed: (msg) => `Le journal n'a pas pu être effacé : ${msg}`,
    forgotten: (text) => `Souvenir oublié : « ${text} »`,
    forgottenMany: (n) => `${n} souvenirs oubliés`,
    undo: "Annuler",
    restored: "Souvenir rétabli.",
    undoFailed: (msg) => `Impossible de le rétablir : ${msg}`,
    undoNotice: "Monsieur a annulé l'oubli depuis l'écran : le souvenir est de nouveau en mémoire.",
  },
  // A.R.E.S seen from JARVIS (ares.js, WP15): the agenda section, the chip, the write cards.
  ares: {
    title: "Agenda A.R.E.S",
    count: (n) => `Agenda A.R.E.S · ${n}`,
    chip: "A.R.E.S",
    up: "A.R.E.S joignable : agenda, tâches et notes à la voix",
    down: "A.R.E.S injoignable : activez son serveur MCP local (A.R.E.S › Réglages › Application de bureau)",
    upShort: "joignable",
    downShort: "injoignable",
    snapshot: (time) => `Instantané de ${time}`,
    reminder: "Rappel",
    late: "en retard",
    card: "A.R.E.S",
  },
  // ---- WP17: « JARVIS a remarqué » (remarques.js)
  remarques: {
    title: "JARVIS a remarqué",
    count: (n) => `JARVIS a remarqué (${n})`,
    why: "Pourquoi ?",
    dismiss: (text) => `Masquer la remarque « ${text} »`,
    dismissed: (n) => (Number(n) > 1 ? `Remarque masquée pendant ${n} jours.` : "Remarque masquée pendant 1 jour."),
    dismissFailed: (why) => `Impossible de masquer la remarque : ${why}`,
    actionFailed: (why) => `Action impossible : ${why}`,
  },
  // Costs (usage.js, WP18): the top-bar chip, the warnings, the cap's
  // confirmation and Réglages › Coûts. Amounts come formatted ('0,42 $').
  usage: {
    chip: (amount) => `Aujourd'hui ≈ ${amount}`,
    chipWarn: (pct) => ` · ${pct}${NBSP}% du plafond`,
    chipCapped: " · plafond atteint",
    tooltip: (voice, claude) => `Voix ≈ ${voice} · Claude ≈ ${claude}\nClaude : estimation (équivalent API)`,
    tooltipCap: (cap) => `\nPlafond du jour : ${cap} · Réglages › Coûts`,
    estimate: "Claude : estimation (équivalent API)",
    warnTitle: "Dépenses du jour",
    warnText: (pct, spent, cap) => `${pct}${NBSP}% du plafond du jour : environ ${spent} sur ${cap}. `
      + "Modifiable dans Réglages › Coûts.",
    capTitle: "Plafond du jour atteint",
    // claude: the Claude part when there is one (an API-price estimate, not billed on a claude.ai plan).
    capText: (spent, cap, claude = "") => `Environ ${spent} estimés aujourd'hui, pour un plafond de ${cap}. `
      + (claude ? `Dont Claude ≈ ${claude} : une estimation au tarif de l'API, non facturée avec un abonnement `
        + "claude.ai, que le plafond compte quand même. " : "")
      + "Le mot d'éveil n'ouvre plus de conversation et aucune nouvelle tâche Claude ne démarre. "
      + "Un clic sur l'orbe reste possible, après confirmation.",
    openSettings: "Réglages › Coûts",
    askTitle: "Plafond du jour atteint",
    askText: (spent, cap) => `Environ ${spent} dépensés aujourd'hui, pour un plafond de ${cap}. `
      + "Cette conversation sera payante, elle aussi.",
    askGeneric: "Le plafond de dépenses du jour est atteint. Cette conversation sera payante, elle aussi.",
    askHint: "Le plafond se modifie dans Réglages › Coûts.",
    askOk: "Ouvrir quand même",
    askCancel: "Annuler",
    // Réglages › Coûts
    title: "30 derniers jours",
    voice: "Voix (OpenAI)",
    claude: "Claude (estimation)",
    today: (total, voice, claude) => `Aujourd'hui ≈ ${total} · voix ≈ ${voice} · Claude ≈ ${claude}`,
    period: (sum, avg) => `Sur 30 jours ≈ ${sum} · en moyenne ≈ ${avg} par jour`,
    capSet: (cap) => `Plafond du jour : ${cap}.`,
    capNone: "Aucun plafond du jour.",
    note: "Claude : estimation (équivalent API). Claude Code calcule ce montant au tarif de l'API ; "
      + "avec un abonnement claude.ai, les tâches ne sont pas facturées à l'unité. "
      + "La voix est estimée d'après les tarifs publics d'OpenAI.",
    chartLabel: "Dépenses des 30 derniers jours, voix et Claude",
    chartLoading: "Chargement du graphique…",
    chartUnavailable: "Graphique indisponible (hors ligne ?) : les chiffres sont dans le tableau.",
    table: "Voir les chiffres",
    colDate: "Jour",
    colVoice: "Voix",
    colClaude: "Claude",
    colTotal: "Total",
    nothing: "Aucune dépense ces 30 derniers jours.",
    loadFailed: "Dépenses illisibles pour l'instant : réessayez dans un moment.",
  },
  help: {
    // What monsieur says to JARVIS (he says "tu" to it): examples, not UI copy.
    examples: [
      "Jarvis, ouvre Spotify sur l'écran de gauche",
      "Baisse le volume à 30 %",
      "Rappelle-moi dans 20 minutes de sortir le pain",
      "Tous les matins à 8 h, fais-moi un point météo",
      "Regarde mon écran : tu vois l'erreur ?",
      "Analyse le fichier ventes.xlsx et fais-moi un tableau de bord",
      "Retiens que je préfère le thé",
      "Cherche les meilleurs aspirateurs robots sous 400 €",
      "Quel temps fera-t-il demain à Lyon ?",
      "Qu'est-ce que j'ai aujourd'hui ?",
      "Note que je dois rappeler le garage",
      "De quoi on a parlé hier ?",
      "Reporte le rappel de 10 minutes",
      "Combien j'ai dépensé aujourd'hui ?",
    ],
    // The "Ce que je sais faire" card: every example is a button that asks it.
    categories: [
      { title: "Applications et PC",
        examples: ["Jarvis, ouvre Spotify sur l'écran de gauche", "Baisse le volume à 30 %"] },
      { title: "Rappels et routines",
        examples: ["Rappelle-moi dans 20 minutes de sortir le pain", "Reporte le rappel de 10 minutes",
                   "Tous les lundis et jeudis à 18 h, rappelle-moi le sport"] },
      { title: "Recherche et fichiers",
        examples: ["Cherche les meilleurs aspirateurs robots sous 400 €",
                   "Analyse le fichier ventes.xlsx et fais-moi un tableau de bord"] },
      { title: "Météo et actualités",
        examples: ["Quel temps fera-t-il demain à Lyon ?", "Quels sont les titres de l'actualité ?"] },
      { title: "Vision",
        examples: ["Regarde mon écran : tu vois l'erreur ?", "Regarde-moi avec la caméra : je suis bien coiffé ?"] },
      { title: "Mémoire et journal", examples: ["Retiens que je préfère le thé", "De quoi on a parlé hier ?"] },
      // get_status: running tasks, coming reminders, today's spending and the cap (WP18).
      { title: "Point du jour",
        examples: ["Qu'est-ce qui tourne en ce moment ?", "Combien j'ai dépensé aujourd'hui ?"] },
      { title: "Agenda A.R.E.S", ares: true,
        examples: ["Qu'est-ce que j'ai aujourd'hui ?", "Note que je dois rappeler le garage"] },
    ],
    // Examples of the PC's own tools (open_app, look_at_screen): never offered on the iPhone.
    pcOnly: ["Jarvis, ouvre Spotify sur l'écran de gauche", "Regarde mon écran : tu vois l'erreur ?"],
    tryThis: (example) => `Essayez : « ${example} »`,
    shortcuts: "Espace : parler · Ctrl+J : écrire · Échap : interrompre · Ctrl+M : micro",
    // The global hotkey (Réglages › Système), when one works on this PC.
    globalKey: (combo) => `${combo} : depuis n'importe où`,
  },
  // JARVIS on iPhone: one block per package, each edited by its owner only.
  // ---- remote: owned by A4
  remote: {
    // Réglages › Accès à distance (remote-settings.js), on the PC and on the paired iPhone.
    loading: "Chargement…",
    notYet: "L'accès à distance n'est pas encore disponible dans cette version de JARVIS.",
    notReady: "Pas encore disponible dans cette version de JARVIS.",
    quoted: (text) => `« ${text} »`,
    copy: "Copier",
    copied: "Copié.",
    copyFailed: "Copie impossible : sélectionnez le texte, puis Ctrl+C.",
    cancel: "Annuler",
    save: "Enregistrer",
    saved: "Enregistré.",
    checking: "Vérification…",
    recheck: "Revérifier",
    // 1. status
    statusTitle: "État",
    tailscale: "Tailscale",
    tsMissing: "non installé sur ce PC",
    tsStopped: "installé, mais arrêté ou déconnecté",
    tsRunning: "installé et connecté",
    address: "Adresse",
    account: "Compte",
    noHost: "aucune",
    noLogin: "aucun",
    hostSource: { env: "(définie dans .env)", saved: "(enregistrée)", detected: "(détectée : confirmée en activant l'accès)", none: "" },
    loginSource: { env: "(défini dans .env)", saved: "(enregistré)", detected: "(détecté : confirmé en activant l'accès)", none: "" },
    personal: "Compte professionnel ? Préférez un compte Tailscale personnel (voir le guide).",
    capMissing: "Aucun plafond de dépense par jour : l'accès à distance en exige un, il protège votre crédit OpenAI.",
    setCap: "Fixer un plafond",
    // 2. the switch
    switchLabel: "Accès à distance",
    switchHelp: "Il peut être refusé sans plafond de dépense par jour (Réglages › Coûts), sans Tailscale connecté sur ce PC, ou sans adresse Tailscale. Le couper retire aussi la publication Tailscale ; le rallumer la rétablit. Aussi depuis l'icône JARVIS près de l'horloge.",
    switching: "Un instant…",
    switchedOn: "Accès à distance activé.",
    switchedOff: "Accès à distance coupé.",
    pausedUntil: (time) => `En pause jusqu'à ${time}.`,
    resume: "Reprendre maintenant",
    resumed: "Accès à distance repris.",
    // 3. Publier sur Tailscale
    serveTitle: "Publication Tailscale",
    serveHelp: "JARVIS se publie sur votre réseau Tailscale seulement, jamais sur internet, avec la commande ci-dessous.",
    publish: "Publier sur Tailscale",
    publishing: "Publication…",
    published: "Publié sur Tailscale.",
    publishFailed: "La publication a échoué.",
    unpublish: "Retirer la publication",
    unpublished: "Publication retirée.",
    consent: "Tailscale demande d'autoriser HTTPS sur votre réseau : ouvrez ce lien, acceptez, puis revenez ici.",
    consentLink: "Autoriser HTTPS sur Tailscale",
    consentBad: "Lien d'autorisation inattendu : il n'est pas affiché. Utilisez la commande manuelle.",
    serveLine: (word) => `Serve : ${word}`,
    serveStates: { ready: "prêt", absent: "absent", funnel: "Funnel actif", tcp: "relais TCP", wrong_target: "cible inattendue",
                   stopped: "Tailscale arrêté", no_tailscale: "Tailscale absent", unknown: "inconnu" },
    manual: "Commande manuelle",
    manualHelp: "À taper dans PowerShell (touche Windows, tapez powershell, Entrée) si le bouton ne suffit pas :",
    fixLabel: "Pour l'arrêter, à taper d'abord dans PowerShell :",
    manualFull: "Si PowerShell ne trouve pas tailscale :",
    // 4. pairing
    pairTitle: "Associer un iPhone",
    pair: "Associer un iPhone",
    pairHelp: "Ouvre l'association pour 10 minutes : l'iPhone demande l'accès et affiche un code à 4 chiffres, que vous vérifiez ici.",
    pairOpenFor: (time) => `Association ouverte encore ${time}.`,
    pairSteps: "Sur l'iPhone : ouvrez cette adresse dans Safari, ou scannez le QR code avec l'Appareil photo, puis suivez les étapes.",
    pairClose: "Fermer l'association",
    pairClosed: "Association fermée.",
    qrLabel: (url) => `QR code de l'adresse ${url}`,
    qrLoading: "QR code en préparation…",
    qrFallback: "QR code indisponible : tapez l'adresse ci-dessus dans Safari sur l'iPhone.",
    requestsLabel: "Demandes d'association",
    noRequest: "Aucune demande pour l'instant : elles s'affichent ici dès que l'iPhone demande l'accès.",
    several: "Plusieurs demandes en attente : n'autorisez que celle dont le code s'affiche sur votre iPhone.",
    codeIs: "Code :",
    allow: "Autoriser",
    deny: "Refuser",
    allowLabel: (code) => `Autoriser la demande ${code}`,
    denyLabel: (code) => `Refuser la demande ${code}`,
    typeCode: "Code affiché sur l'iPhone (4 chiffres)",
    codeFormat: "Tapez les 4 chiffres affichés sur l'iPhone.",
    approvedWait: "Autorisée : l'iPhone termine l'association…",
    allowed: (code) => `Demande ${code} autorisée : l'iPhone termine l'association.`,
    denied: (code) => `Demande ${code} refusée.`,
    // 5. devices
    devicesTitle: "Appareils associés",
    noDevices: "Aucun appareil associé.",
    rename: "Renommer",
    renameLabel: (name) => `Renommer ${name}`,
    newName: "Nouveau nom",
    renamed: (name) => `Renommé « ${name} ».`,
    revoke: "Retirer",
    revokeLabel: (name) => `Retirer ${name}`,
    revokeTitle: (name) => `Retirer « ${name} » ?`,
    revokeAsk: "Cet appareil ne pourra plus joindre JARVIS : pour l'utiliser à nouveau, il faudra l'associer depuis ce PC.",
    revokeWarn: "Ses clés Siri et ses tâches en cours s'arrêtent aussi. Si les notifications ntfy servent, leur sujet change : il ne les reçoit plus, et vos autres iPhone devront s'abonner au nouveau sujet.",
    revoked: (name, count) => `« ${name} » retiré. Tâches arrêtées : ${count}.`,
    topicRenewed: "Nouveau sujet de notifications : sur vos autres iPhone, abonnez-vous-y (Réglages › Notifications).",
    pairedOn: (date) => `Associé le ${date}`,
    lastSeen: (when) => `vu ${when}`,
    neverSeen: "pas encore vu",
    // 6. full access from the iPhone
    completTitle: "Accès complet depuis l'iPhone",
    complet: { never: "Jamais", day: "24 h", week: "7 jours" },
    completUntil: (when) => `Autorisé jusqu'au ${when}.`,
    completNever: "Jamais : les tâches avec accès complet se lancent depuis le PC seulement.",
    completHelp: "Même autorisée, une tâche avec accès complet demandée depuis l'iPhone attend toujours le bouton Lancer sur l'iPhone, jamais un « oui » à la voix, et le PC en est averti. Elle est refusée après des données venues d'internet.",
    completAsk: (label) => `Autoriser les tâches avec accès complet depuis l'iPhone pendant ${label} ? Chacune attendra le bouton Lancer sur l'iPhone, et le PC en sera averti.`,
    completOk: "Autoriser",
    // 7. recent activity (the audit, in plain French)
    auditTitle: "Activité récente",
    auditRefresh: "Actualiser",
    auditEmpty: "Aucune activité à distance pour l'instant.",
    kindWhat: (kind, what) => `${kind} : ${what}`,
    times: (n) => `(${n} fois)`,
    accepted: "acceptée",
    refused: "refusée",
    callers: { pc: "PC", app: "iPhone", siri: "Siri", unpaired: "appareil non associé" },
    kinds: { request: "Requête", pair: "Association", device: "Appareil", state: "Accès", tool: "Outil", decide: "Décision",
             alert: "Alerte", usage: "Consommation", key: "Clé Siri" },
    reasons: {
      funnel: "refusée : venue d'internet (Funnel)", proxy_on_pc_port: "refusée : proxy sur le port du PC",
      off: "refusée : accès coupé", paused: "refusée : accès en pause", host: "refusée : adresse inattendue",
      proto: "refusée : sans HTTPS", xff: "refusée : adresse hors Tailscale", self: "refusée : adresse du PC lui-même",
      login: "refusée : compte non autorisé", origin: "refusée : origine inattendue", site: "refusée : autre site",
      locked: "refusée : bloqué après des échecs", token: "refusée : jeton expiré", revoked: "refusée : appareil retiré",
      ip: "refusée : autre machine", unpaired: "refusée : appareil non associé", scope: "refusée : réservé au PC",
      rate: "refusée : trop de demandes", cap: "refusée : plafond atteint", clamped: "relevé plafonné",
      throttled: "lignes regroupées", serve_unsafe: "refusée : relais TCP ou Funnel vers JARVIS",
    },
    // the paired iPhone's own view
    thisPhone: "Cet iPhone",
    phoneActive: "Accès à distance actif",
    phoneComplet: (when) => `Accès complet autorisé par le PC jusqu'au ${when}.`,
    pauseHelp: "Mettre en pause coupe tout l'accès à distance (vos iPhone et Siri) ; seul le PC peut le rallumer avant la fin de la pause.",
    pause: "Mettre en pause",
    pauseTitle: "Couper l'accès depuis l'iPhone ?",
    pauseText: "Seul le PC pourra le rallumer avant la fin de la pause.",
    pause1: "1 h",
    pause24: "24 h",
    pausedNow: (time) => `Accès à distance en pause jusqu'à ${time}. Seul le PC peut le rallumer avant.`,
    forget: "Oublier cet iPhone",
    forgetTitle: "Oublier cet iPhone ?",
    forgetAsk: "JARVIS ne reconnaîtra plus cet iPhone : pour l'utiliser à nouveau, il faudra l'associer depuis le PC.",
    forgetWarn: "Ses clés Siri et ses tâches en cours s'arrêtent aussi.",
    forgotten: "iPhone oublié.",
  },
  // ---- pair: owned by A4
  pair: {
    // The pairing page (pair.js): before the PC has allowed this device.
    title: "Associer cet appareil",
    intro: "JARVIS tourne sur votre PC. Cet appareil doit y être autorisé une fois : donnez-lui un nom, puis demandez l'accès.",
    installTitle: "D'abord, ajoutez JARVIS à l'écran d'accueil",
    installIntro: "L'association se fait dans l'app web JARVIS, celle que vous ouvrirez ensuite.",
    installSteps: [
      "Dans Safari, touchez ⋯ (en bas de l'écran), puis Partager.",
      "Touchez Sur l'écran d'accueil.",
      "Laissez « Ouvrir comme app web » activé, puis touchez Ajouter.",
      "Ouvrez l'icône JARVIS sur l'écran d'accueil : l'association continue là.",
    ],
    useSafari: "Utiliser JARVIS dans Safari",
    safariNote: "Dans Safari, JARVIS fonctionne aussi ; l'icône de l'écran d'accueil devra alors être associée à part.",
    nameLabel: "Nom de cet appareil",
    nameDefault: "iPhone",
    nameHelp: "Il s'affichera sur le PC, dans Réglages › Accès à distance.",
    ask: "Demander l'accès",
    asking: "Demande en cours…",
    failed: "La demande n'a pas abouti : réessayez.",
    codeTitle: "Code de cette demande",
    codeLabel: (digits) => `Code de cette demande : ${digits}`,
    check: "Sur le PC, vérifiez que le même code s'affiche, puis cliquez Autoriser.",
    waiting: "En attente de l'accord du PC…",
    lost: "Le PC ne répond pas : nouvel essai dans un instant…",
    approved: "Associé !",
    opening: "JARVIS s'ouvre…",
    endTitle: "Association interrompue",
    denied: "La demande a été refusée sur le PC.",
    expired: "La demande a expiré : l'association s'est refermée sur le PC.",
    restart: "Recommencer",
    retry: "Réessayer",
    titles: {
      closed: "Association fermée",
      off: "Accès à distance coupé",
      paused: "Accès à distance en pause",
      refused: "Accès refusé",
      locked: "Accès bloqué",
      revoked: "Appareil retiré",
    },
    states: {
      closed: "L'association est fermée. Sur le PC : Réglages › Accès à distance › Associer un iPhone, puis touchez Réessayer.",
      off: "L'accès à distance est coupé sur le PC. Il se rallume sur le PC, dans Réglages › Accès à distance ou depuis l'icône JARVIS près de l'horloge.",
      paused: "L'accès à distance est en pause. Il reprendra seul à la fin de la pause, ou plus tôt depuis le PC.",
      refused: "Vérifiez que cet appareil est connecté à Tailscale avec le même compte que le PC, puis ouvrez JARVIS depuis son icône sur l'écran d'accueil ou en tapant son adresse dans Safari.",
      locked: "Trop d'échecs depuis cet appareil : réessayez dans 15 minutes.",
      revoked: "Cet appareil a été retiré sur le PC. Pour l'associer à nouveau : Réglages › Accès à distance › Associer un iPhone, sur le PC.",
    },
  },
  // ---- notify: owned by B1
  notify: {
    loading: "Chargement…",
    notYet: "Pas encore disponible.",
    intro: "JARVIS envoie un mot sur l'iPhone par l'app ntfy quand une tâche se termine, qu'un rappel sonne ou qu'une confirmation attend, et à chaque alerte de sécurité. Jamais le contenu d'une tâche, jamais un lien.",
    topicTitle: "Sujet ntfy",
    topicHelp: "Ce sujet est l'adresse de vos notifications : qui le connaît peut les lire. Ne le partagez pas.",
    copyTopic: "Copier le sujet",
    copied: "Sujet copié : collez-le dans l'app ntfy.",
    copyFailed: "Copie impossible : touchez le sujet pour le sélectionner, puis copiez-le.",
    server: (url) => `Serveur ntfy : ${url}`,
    test: "Envoyer un test",
    sending: "Envoi…",
    sent: "Test envoyé : regardez l'iPhone.",
    newTopic: "Nouveau sujet",
    newTitle: "Nouveau sujet ntfy ?",
    newAsk: "Un nouveau sujet remplace l'actuel : il faudra le coller de nouveau dans l'app ntfy de l'iPhone.",
    newWarn: "L'ancien sujet ne recevra plus aucune notification.",
    newDone: "Nouveau sujet créé : collez-le dans l'app ntfy de l'iPhone.",
    on: "Notifications activées.",
    off: "Notifications coupées. Elles s'activent sur le PC, dans Réglages › Notifications.",
    offPc: "Notifications coupées : cochez la première case pour les activer.",
    lastSent: (when) => `Dernier envoi réussi : ${when}.`,
    lastError: (text) => `Dernier échec : ${text}`,
    guideTitle: "Sur cet iPhone, en 4 étapes",
    step1: "Installez l'app ntfy depuis l'App Store et autorisez ses notifications.",
    step2: "Dans ntfy, touchez +.",
    step3: "Collez le sujet copié ci-dessus.",
    step3Server: (server) => `Collez le sujet copié ci-dessus, avec le serveur ${server}.`,
    step4: "Touchez « S'abonner » (« Subscribe » si l'app est en anglais).",
  },
  // ---- siri: owned by B3
  siri: {
    // On the PC, under each paired iPhone (Réglages › Accès à distance)
    title: "Raccourci Siri",
    create: "Créer une clé Siri",
    creating: "Création…",
    createHelp: "Une clé laisse le raccourci « Jarvis » de cet iPhone parler à JARVIS avec Siri : rappels, recherches web, état et annulation de ses tâches. Siri ne répond qu'avec un plafond de dépense par jour (Réglages › Coûts).",
    created: (time) => `Clé créée. Sur l'iPhone, avant ${time} : Réglages › Accès à distance › Assistant raccourci.`,
    maxKeys: (n) => `${n} clés au plus par iPhone : révoquez-en une pour en créer une autre.`,
    keyLabel: (id) => `Clé ${id}`,
    keyCreated: (when) => `créée le ${when}`,
    keyUsed: (when) => `utilisée ${when}`,
    keyUnused: "jamais utilisée",
    revoke: "Révoquer",
    revokeLabel: (when) => `Révoquer la clé Siri créée le ${when}`,
    revokeTitle: "Révoquer cette clé Siri ?",
    revokeAsk: "Le raccourci Siri qui l'utilise ne marchera plus. Pour Siri de nouveau, créez une autre clé.",
    revoked: "Clé Siri révoquée.",
    // On the iPhone (the phone view of the same section)
    phoneHelp: "Pour parler à JARVIS avec Siri : sur le PC, Réglages › Accès à distance › cet iPhone › Créer une clé Siri. Puis, dans les 10 minutes, ouvrez l'assistant ici.",
    assistant: "Assistant raccourci",
    loading: "Récupération de la clé…",
    none: "Aucune clé Siri en attente : créez-en une sur le PC (Réglages › Accès à distance), puis rouvrez l'assistant dans les 10 minutes.",
    once: "La clé ne s'affiche qu'une fois : copiez chaque valeur dans le raccourci avant de fermer. Ne partagez jamais ce raccourci.",
    rowUrl: "Adresse (URL)",
    rowHeader: "Nom de l'en-tête",
    rowValue: "Valeur de l'en-tête",
    rowField: "Champ du corps JSON",
    copy: "Copier",
    copyLabel: (what) => `Copier : ${what}`,
    copied: (what) => `${what} : copié.`,
    copyFailed: "Copie impossible : touchez et maintenez le texte pour le copier.",
    recipeTitle: "Le raccourci, en 3 actions",
    recipeStart: "App Raccourcis › + (sur iOS 27, ignorez « Décrire un raccourci ») › nommez-le Jarvis.",
    recipe: [
      "Dicter le texte (langue : Français).",
      "Obtenir le contenu de l'URL : collez l'adresse ; « En afficher plus » › Méthode POST ; En-têtes › Ajouter un nouvel en-tête : Authorization = la valeur copiée ; Corps de la requête (ou « Demander le corps ») JSON › Ajouter un nouveau champ › Texte : text = Texte dicté.",
      "Énoncer le texte (le contenu de l'URL).",
    ],
    recipeEnd: "Premier lancement : répondez « Toujours autoriser ». Puis dites « Dis Siri, Jarvis ».",
    close: "Fermer",
    // Réglages › Coûts and the cost chip (usage.js), once Siri spent something
    costToday: (amount) => ` · Siri ≈ ${amount}`,
    costTooltip: (amount) => `\nSiri ≈ ${amount}`,
    costSeries: "Siri (OpenAI, texte)",
    costColumn: "Siri",
  },
  // ---- ios: owned by B2
  ios: {
    // A finger, no keyboard (touchUI()): the status line, the composer and the
    // help card without Espace, Ctrl+J, Ctrl+M or Échap (hud.js, keys.js).
    standby: "En veille · touchez l'orbe pour parler",
    standbyWake: "En veille · dites « Jarvis » ou touchez l'orbe",
    wakeOff: "En veille · mot d'éveil désactivé · touchez l'orbe",
    off: "Hors ligne · touchez l'orbe pour parler",
    speaking: "JARVIS répond · parlez ou touchez Interrompre",
    muted: "Micro coupé · touchez « Micro » pour le réactiver",
    composerPlaceholder: "Écrivez à JARVIS…",
    gestures: "Commandes tactiles",
    help: "Orbe : parler ou mettre en veille · Champ de message : écrire · Interrompre : couper la parole · Micro : couper le micro",
    // The conversation and iOS (ios.js, voice.js).
    paused: "Conversation en pause (écran verrouillé ou autre app).",
    resume: "Reprendre",
    dismiss: "Fermer ce message",
    micInterrupted: "Micro interrompu (appel, Siri ou autre app) : JARVIS vous entend de nouveau dès qu'iOS le rend.",
    // The microphone refused or busy, on the iPhone (no address bar, no Windows): explainError, voice.js.
    micBlocked: "Micro refusé : réessayez et autorisez le micro quand iOS le demande. S'il ne le demande plus : Réglages de l'iPhone › Apps › Safari › Micro.",
    micBusy: "Micro occupé par un appel ou une autre app : réessayez une fois libéré.",
    // Réglages › Écoute on iOS: Safari cannot choose the output (settings.js).
    speakerNote: "Sur l'iPhone, iOS choisit la sortie du son : haut-parleur, écouteurs ou AirPlay (Centre de contrôle).",
  },
});

// The Mise en route says a missing or refused microphone in the error table's words.
T.onboarding.mic.none = T.error.NotFoundError;
T.onboarding.mic.denied = T.error.NotAllowedError;

/* The status line while a tool runs ('' when there is no label for it). */
export function toolLabel(name, args = {}) {
  const label = T.tool[name];
  return typeof label === "function" ? label(args ?? {}) : label || "";
}

/* ---------------------------------------------------------- formatting (fr-FR) */
const asDate = (d) => (d instanceof Date ? d : new Date(d ?? Date.now()));
const pad2 = (n) => String(n).padStart(2, "0");

/* '14 h 30', '8 h' (no-break spaces: a time never wraps). */
export function fmtTime(d = new Date()) {
  const t = asDate(d), h = t.getHours(), m = t.getMinutes();
  return m ? `${h}${NBSP}h${NBSP}${pad2(m)}` : `${h}${NBSP}h`;
}

/* Elapsed seconds as 'm:ss' (or 'h:mm:ss'), for running tasks. */
export function fmtElapsed(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}:${pad2(m)}:${pad2(s % 60)}` : `${m}:${pad2(s % 60)}`;
}

const numberFormats = new Map();
export function fmtNumber(n, options = {}) {
  const key = JSON.stringify(options);
  if (!numberFormats.has(key)) numberFormats.set(key, new Intl.NumberFormat("fr-FR", options));
  return numberFormats.get(key).format(n);
}

const dayFormat = new Intl.DateTimeFormat("fr-FR", { weekday: "short", day: "numeric", month: "short" });

/* 'à l'instant', 'il y a 3 min', 'il y a 2 h', 'hier à 9 h 05', 'dans 5 min'... */
export function fmtRelative(when, now = Date.now()) {
  const t = asDate(when), ref = asDate(now);
  const diff = (ref - t) / 1000;  // seconds, > 0 in the past
  const abs = Math.abs(diff);
  if (abs < 45) return T.time.justNow;
  if (abs < 3600) {
    const n = Math.max(1, Math.round(abs / 60));
    return diff > 0 ? T.time.minutesAgo(n) : T.time.inMinutes(n);
  }
  const days = Math.round((startOfDay(t) - startOfDay(ref)) / 86400e3);
  if (abs < 6 * 3600) {
    const n = Math.round(abs / 3600);
    return diff > 0 ? T.time.hoursAgo(n) : T.time.inHours(n);
  }
  if (days === 0) return T.time.at(fmtTime(t));
  if (days === -1) return T.time.yesterday(fmtTime(t));
  if (days === 1) return T.time.tomorrow(fmtTime(t));
  return T.time.onDate(dayFormat.format(t), fmtTime(t));
}
function startOfDay(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime(); }

/* ---------------------------------------------------------- errors */
// Bus error kinds (or codes) that map straight to a message.
const ERROR_KINDS = {
  lost: "lost", wake_refused: "wakeRefused", wakeRefused: "wakeRefused", inaudible: "inaudible",
  content_filter: "contentFilter", contentFilter: "contentFilter", image: "image",
  claude_missing: "claudeMissing", claudeMissing: "claudeMissing", claude_logged_out: "claudeLoggedOut",
  claudeLoggedOut: "claudeLoggedOut", sandbox: "sandbox", no_key: "noKey", noKey: "noKey",
  timeout: "timeout", network: "network", server: "server", quota: "quota", rate: "rate",
  model: "model", unauthorized: "unauthorized", insufficient_quota: "quota",
  rate_limit_exceeded: "rate", model_not_found: "model", invalid_api_key: "unauthorized",
};

let ours = null;
const OURS = () => ours || (ours = new Set([...Object.values(T.error), T.ios.micBlocked, T.ios.micBusy]
  .filter(v => typeof v === "string")));

/* The paired iPhone's page, or any page on iOS: the microphone is not
   unblocked from an address bar or from Windows there. (This module stays
   pure: node runs it, without a document.) */
export function onPhone() {
  const doc = typeof document !== "undefined" ? document : null;
  const nav = typeof navigator !== "undefined" ? navigator : null;
  if (doc?.querySelector?.('meta[name="jarvis-remote"]')?.getAttribute("content") === "1") return true;
  return !!nav && (/iPhone|iPad|iPod/.test(nav.userAgent || "")
    || (nav.platform === "MacIntel" && nav.maxTouchPoints > 1));
}

/* The French message for an error (design spec §11 errors table): a bus error
   {kind, message, detail}, an Error or DOMException, or a string. A message
   that is already one of ours comes back unchanged. */
export function explainError(err) {
  if (err == null || err === "") return T.error.unknown;
  if (typeof err !== "object") err = { message: String(err) };
  const src = err.detail && typeof err.detail === "object" ? err.detail : {};
  const name = String(err.name || src.name || "");
  const kind = String(err.code || err.kind || src.code || "");
  const status = Number(err.status ?? src.status) || 0;
  const msg = String(err.message || src.message || "").trim();

  if (ERROR_KINDS[kind]) return T.error[ERROR_KINDS[kind]];
  // Already explained (voice.js explains its own errors; the server adds
  // "(OpenAI 429)" to the same words): kept as is, diagnosis included.
  const bare = msg.replace(/\s*\(OpenAI \d{3}\)\s*$/, "");
  if (bare && OURS().has(fr(bare))) return fr(msg);
  if (/^(NotAllowedError|SecurityError|PermissionDeniedError)$/.test(name)) {
    return onPhone() ? T.ios.micBlocked : T.error.NotAllowedError;
  }
  if (/^(NotFoundError|OverconstrainedError|DevicesNotFoundError)$/.test(name)) return T.error.NotFoundError;
  if (/^(NotReadableError|TrackStartError)$/.test(name)) return onPhone() ? T.ios.micBusy : T.error.NotReadableError;
  if (name === "AbortError" || name === "TimeoutError") return T.error.timeout;
  if (/OPENAI_API_KEY|cl[ée] (openai )?(absente|manquante)|no api key/i.test(msg)) return T.error.noKey;
  const openai = /OpenAI (\d{3})\b/.exec(msg);
  const code = openai ? Number(openai[1]) : status;
  if (/insufficient_quota|exceeded your current quota|billing_hard_limit/i.test(msg)) return T.error.quota;
  if (code === 429 || /rate.?limit/i.test(msg)) return T.error.rate;
  if ((openai && code === 401) || /invalid_api_key|incorrect api key/i.test(msg)) return T.error.unauthorized;
  if (/model_not_found|does not exist|do(es)? not have access|unknown model|invalid model/i.test(msg)) return T.error.model;
  if (/content_filter/i.test(msg)) return T.error.contentFilter;
  if (/OpenAI injoignable|getaddrinfo|ENOTFOUND|ECONNREFUSED|ConnectError|api\.openai\.com/i.test(msg)) return T.error.network;
  if (name === "TypeError" && /fetch|network|load failed/i.test(msg)) {
    return typeof navigator !== "undefined" && navigator.onLine === false ? T.error.network : T.error.server;
  }
  if (/timed? ?out\b/i.test(msg)) return T.error.timeout;
  return msg ? fr(msg) : T.error.unknown;
}

export function init() {}
