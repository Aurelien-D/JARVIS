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
    info: (a) => (a && /actu|news/i.test(a.kind || a.topic || "") ? "Je consulte l'actualité…" : "Je consulte la météo…"),
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
    agendaDown: "A.R.E.S n'est pas joignable : activez son serveur MCP local (Réglages › Application de bureau).",
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
    sectionTitle: (n) => `Sessions Claude Code · ${n} en cours`,
    history: (n) => `Historique (${n})`,
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
    useThisPage: "Utiliser celle-ci",
    otherTitle: "Autre fenêtre",
    dndHour: "Ne pas déranger 1 h",
    dndEnd: "Arrêter « Ne pas déranger »",
    dndFailed: "« Ne pas déranger » n'a pas pu être enregistré : réessayez.",
    notifTitle: "Notifications",
    warning: "Attention",
    reminder: "Rappel",
    reminderLate: (minutes) => ` (en retard de ${minutes} min)`,
    reminderAt: (time) => `Rappel (${time})`,
  },
  wake: {
    title: "Mot d'éveil",
    local: "écoute locale",
    cloud: "écoute via Google",
    paused: "en pause (micro refusé)",
    elsewhere: "dans l'autre fenêtre",
    denied: "Micro refusé dans le navigateur : mot d'éveil désactivé. Autorisez le micro (cadenas de la barre d'adresse › Microphone), puis réactivez le mot d'éveil.",
    installed: "Pack vocal français hors-ligne installé.",
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
    deadlines: "OpenAI arrête gpt-realtime le 20 janvier 2027 et whisper-1 le 26 février 2027.",
    buttons: { recheck: "Revérifier", testMic: "Tester le micro", testVoice: "Tester la voix", finish: "Terminer" },
  },
  settings: {
    sections: ["Voix", "Écoute", "Proactivité", "Claude Code", "Coûts", "Système", "Données", "À propos"],
    eagernessLow: "Il me coupe trop tôt",
    sensitive: "Réglage sensible : il ne peut être modifié qu'ici, jamais à la voix.",
    bypass: "Mode sans garde-fou : Claude peut tout modifier sans contrôle. Déconseillé.",
    data: "Ce qui part où : votre voix pendant une conversation → OpenAI ; en veille → reconnaissance locale de Chrome, ou Google si elle n'est pas disponible ; captures d'écran et caméra → OpenAI, seulement quand vous le demandez ; tâches → Claude (Anthropic) ; journal, mémoire et rappels → ce PC (dossier data).",
    purgeJournal: "Purger le journal",
    openData: "Ouvrir le dossier data",
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
      "Quel temps fera-t-il demain à Laon ?",
      "Qu'est-ce que j'ai aujourd'hui ?",
      "Note que je dois rappeler le garage",
      "De quoi on a parlé hier ?",
    ],
    // The "Ce que je sais faire" card: every example is a button that asks it.
    categories: [
      { title: "Applications et PC",
        examples: ["Jarvis, ouvre Spotify sur l'écran de gauche", "Baisse le volume à 30 %"] },
      { title: "Rappels et routines",
        examples: ["Rappelle-moi dans 20 minutes de sortir le pain", "Tous les matins à 8 h, fais-moi un point météo"] },
      { title: "Recherche et fichiers",
        examples: ["Cherche les meilleurs aspirateurs robots sous 400 €",
                   "Analyse le fichier ventes.xlsx et fais-moi un tableau de bord",
                   "Quel temps fera-t-il demain à Laon ?"] },
      { title: "Vision",
        examples: ["Regarde mon écran : tu vois l'erreur ?", "Regarde-moi avec la caméra : je suis bien coiffé ?"] },
      { title: "Mémoire et journal", examples: ["Retiens que je préfère le thé", "De quoi on a parlé hier ?"] },
      { title: "Agenda A.R.E.S", examples: ["Qu'est-ce que j'ai aujourd'hui ?", "Note que je dois rappeler le garage"] },
    ],
    tryThis: (example) => `Essayez : « ${example} »`,
    shortcuts: "Espace : parler · Ctrl+J : écrire · Échap : interrompre · Ctrl+M : micro · Ctrl+Alt+Maj+J : depuis n'importe où",
  },
});

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
const OURS = () => ours || (ours = new Set(Object.values(T.error).filter(v => typeof v === "string")));

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
  // Already explained: one of ours (voice.js explains its own errors), or our
  // server's own French words (voice.js tags those where: 'server').
  if (msg && (OURS().has(fr(msg)) || (src.where === "server" && (err.status ?? src.status)))) return fr(msg);
  if (/^(NotAllowedError|SecurityError|PermissionDeniedError)$/.test(name)) return T.error.NotAllowedError;
  if (/^(NotFoundError|OverconstrainedError|DevicesNotFoundError)$/.test(name)) return T.error.NotFoundError;
  if (/^(NotReadableError|TrackStartError)$/.test(name)) return T.error.NotReadableError;
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
