/* Video Dubbing V3 - single-page interface (vanilla JS, no build step). */
"use strict";
(() => {
  // ------------------------------------------------------------------ helpers
  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (x, d = 2) => (x === null || x === undefined ? "—" : Number(x).toFixed(d));

  const STAGES = ["download", "probe", "extract", "transcribe", "translate", "voice", "timing", "assemble", "export", "validate"];
  const MODES = ["normal", "concise_rewrite", "speed_up", "expanded_rewrite", "slow_down", "padded"];
  const ROLES = ["analysis", "script", "voice"];
  const KEY_ROLES = ["analysis", "script", "voice", "default"];
  const RUNNING = ["running", "review", "cancelling"];
  const NUM_FIELDS = [
    ["MAX_SPEED_UP", 0.01], ["MAX_SLOW_DOWN", 0.01], ["SAFETY_GAP", 0.01],
    ["REWRITE_TRIES", 1], ["ORIGINAL_AUDIO_VOLUME", 0.05],
  ];
  // "Saving money" card (MAX_SPEND_PER_VIDEO may be empty = no limit)
  const ECON_FIELDS = [
    ["ECONOMY_SPEEDUP_LIMIT", 0.01], ["ECONOMY_SLOWDOWN_LIMIT", 0.01],
    ["MAX_SPEND_PER_VIDEO", 0.5], ["CACHE_MAX_GB", 0.5],
  ];

  // ------------------------------------------------------------------ texts
  const I18N = {
    en: {
      tagline: "YouTube → dubbed MP4", spend_title: "Spend (estimate)", spend_job: "This job", spend_month: "This month",
      spend_all: "All time", role_analysis: "Analysis", role_script: "Script", role_voice: "Voice", total: "Total",
      nav_dub: "Dub a video", nav_settings: "Settings", quit: "Quit",
      fake_banner: "Offline test mode: fake AI and a generated test video. No API calls, no real cost.",
      new_title: "New dubbing", url_label: "YouTube video URL", lang_label: "Target language",
      review_label: "Review script before voicing", review_hint: "Pause after translation so you can edit each line.",
      start: "START", cancel: "Cancel", pipeline_title: "Pipeline",
      status_idle: "Ready", status_running: "Working", status_review: "Waiting for you", status_cancelling: "Cancelling…",
      status_done: "Finished", status_failed: "Failed", status_cancelled: "Cancelled",
      progress_idle: "Waiting for a video.", error_title: "Something went wrong", cancelled_title: "Job cancelled",
      open_settings: "Open Settings", close: "Close", final_ready: "FINAL VIDEO READY", dl_mp4: "Download MP4",
      dl_srt: "Subtitles (SRT)", dl_json: "Timing report (JSON)", open_folder: "Open output folder", saved_in: "Saved in",
      quality_checks: "Quality checks", new_video: "New video", review_title: "Review the script",
      review_text: "The translation is ready. Edit any line in the table below, then click Continue to generate the voice.",
      continue: "Continue", video_title: "Video analysis", segments_title: "Speech segments",
      segments_empty: "The parts of the video appear here after the transcription.",
      col_time: "Time", col_slot: "Slot", col_source: "Original", col_translation: "Translation", col_voice: "Voice",
      col_speed: "Speed", col_mode: "Timing", log_title: "Live log",
      keys_title: "OpenAI API keys",
      keys_help: "Use a separate key for each job to see and limit what each one spends on platform.openai.com. Leave a key empty to use the default key. Keys are stored only on this computer and are only sent to OpenAI.",
      models_title: "AI models and voices", model_analysis: "Analysis model (transcription)",
      model_script: "Script model (translation)", model_voice: "Voice model (TTS)",
      analysis_model_note: "The analysis model must return segment timestamps (whisper-1 does).",
      voices_title: "Voice for each language", timing_title: "Timing", output_title: "Output folder",
      browse: "Browse...", open: "Open", output_hint: "Empty = the \"output\" folder next to the app.",
      prices_title: "Prices (estimates)",
      prices_help: "Used only to estimate spend. Check openai.com/api/pricing and correct them if needed. USD.",
      price_model: "Model", price_minute: "$ / audio minute", price_in: "$ / 1M input tokens",
      price_out: "$ / 1M output tokens", price_chars: "$ / 1M characters", add_model: "+ Add a model",
      counters_title: "Spend counters",
      counters_help: "Counters are estimates computed on this computer. They do not replace your OpenAI invoice.",
      reset_counters: "Reset counters", discard: "Discard changes", save: "Save settings",
      stopped_title: "The app is stopped", stopped_text: "You can close this window. To start again, double-click run.bat.",
      lost_title: "Connection lost",
      stage_download: "Download", stage_probe: "Analyse video", stage_extract: "Extract audio",
      stage_transcribe: "Transcribe", stage_translate: "Translate", stage_voice: "Voice",
      stage_timing: "Timing fit", stage_assemble: "Build audio", stage_export: "Export MP4", stage_validate: "Quality check",
      sdesc_download: "Gets the video from YouTube (best MP4 quality, max 1080p).",
      sdesc_probe: "Reads duration, resolution, frame rate and codecs.",
      sdesc_extract: "Takes the sound out of the video for the speech recognition.",
      sdesc_transcribe: "Analysis key · finds every sentence and when it is spoken.",
      sdesc_translate: "Script key · translates the sentences in batches, so each one has its context.",
      sdesc_voice: "Voice key · reads each translated line aloud.",
      sdesc_timing: "Makes every line fit its time: shorter/longer rewrites, then speed 0.82×–1.35×.",
      sdesc_assemble: "Places every line at its original time on one audio track.",
      sdesc_export: "Joins the original picture with the new voice; writes SRT and JSON.",
      sdesc_validate: "Checks that the MP4 opens, has sound, the right length and no overlaps.",
      st_waiting: "waiting", st_running: "running", st_done: "done", st_failed: "failed",
      mode_normal: "normal", mode_concise_rewrite: "shorter rewrite", mode_speed_up: "sped up",
      mode_expanded_rewrite: "fuller rewrite", mode_slow_down: "slowed down", mode_padded: "padded",
      seg_waiting: "waiting", seg_translated: "translated", seg_voiced: "voiced", legend_pause: "pause / free time",
      legend_pending: "not finished",
      chip_duration: "Duration", chip_resolution: "Resolution", chip_fps: "Frame rate", chip_vcodec: "Video codec",
      chip_audio: "Audio", chip_format: "Container", no_audio: "none",
      key_analysis: "Analysis key", key_script: "Script key", key_voice: "Voice key", key_default: "Default key",
      keydesc_analysis: "Transcription: listens to the video (Whisper).",
      keydesc_script: "Translation and length rewrites (GPT).",
      keydesc_voice: "Text-to-speech: the new voice.",
      keydesc_default: "Optional. Used for any job whose key above is empty.",
      show: "Show", hide: "Hide", test_key: "Test key", testing: "Testing…", remove: "Remove",
      src_settings: "Saved key: {m}", src_env: "Using Windows variable {env}: {m}", src_default: "Using the default key: {m}",
      src_missing: "No key yet: paste one here, or set the default key.", src_missing_default: "Not set (optional).",
      ph_replace: "Paste a new key to replace it", ph_paste: "sk-...",
      f_MAX_SPEED_UP: "Max speed-up", h_MAX_SPEED_UP: "Fastest playback of a voice line (1.35 = 35% faster).",
      f_MAX_SLOW_DOWN: "Max slow-down", h_MAX_SLOW_DOWN: "Slowest playback of a voice line (0.82 = 18% slower).",
      f_SAFETY_GAP: "Safety gap (s)", h_SAFETY_GAP: "Silence kept before the next line starts.",
      f_REWRITE_TRIES: "Rewrite attempts", h_REWRITE_TRIES: "Shorter / fuller rewrites tried per line.",
      f_ORIGINAL_AUDIO_VOLUME: "Original sound volume", h_ORIGINAL_AUDIO_VOLUME: "Original soundtrack under the dub (0 = off, 0.15 = quiet).",
      default_is: "default {v}",
      saved_ok: "Settings saved.", confirm_reset: "Reset all spend counters to zero? (A backup of the ledger is kept.)",
      confirm_quit: "A video is still being processed. Quit anyway?", confirm_cancel: "Stop this job?",
      reset_done: "Counters reset.", price_unknown: "price unknown", calls: "{n} API calls",
      elapsed: "Elapsed: {t}", segments_count: "{n} segments · {d} done", job_cost: "Cost of this job",
      sd_title: "Segment {n}", sd_start: "Start", sd_end: "Original end", sd_natural: "Spoken time", sd_slot: "Slot",
      sd_slot_end: "Slot ends", sd_tts: "Voice length", sd_final: "Placed length", sd_stretch: "Speed", sd_mode: "Timing",
      sd_attempts: "API calls", sd_steps: "Steps", sd_original: "Original", sd_translation: "Translation",
      sd_play_original: "Original voice", sd_play_dub: "New voice", sd_no_audio: "Not generated yet.",
      need_url: "Please paste a YouTube video URL.", folder_opened: "Folder opened.", warnings: "Warnings",
      key_ok_saved: "Key removed.", none: "—",
      economy_title: "Saving money",
      economy_help: "These options lower what each video costs without a noticeable change in the result.",
      economy_label: "Economy mode (stretch first)",
      economy_hint: "A line that is a little too long or too short is just sped up or slowed down. It is only rewritten and voiced again when the speed change would be too big. Off = always rewrite first.",
      cache_label: "Reuse what was already paid for (cache)",
      cache_hint: "Transcripts, translations and voice clips are kept on this computer. Re-running a video, or dubbing it into another language, does not pay twice.",
      clear_cache: "Clear cache", cache_cleared: "Cache cleared.",
      confirm_clear_cache: "Delete all cached transcripts, translations and voice clips? Re-running a video will then pay again.",
      cache_info: "{size} used · {n} saved results", model_rewrite: "Rewrite model (length fixes)",
      model_rewrite_ph: "empty = same as the script model",
      f_ECONOMY_SPEEDUP_LIMIT: "Rewrite a long line above", h_ECONOMY_SPEEDUP_LIMIT: "Economy mode: a line needing more speed-up than this (1.15 = 15% faster) is rewritten shorter; below it, it is only sped up.",
      f_ECONOMY_SLOWDOWN_LIMIT: "Rewrite a short line below", h_ECONOMY_SLOWDOWN_LIMIT: "Economy mode: a line needing more slow-down than this to reach its minimum length is rewritten fuller; above it, it is only slowed down.",
      f_MAX_SPEND_PER_VIDEO: "Max spend per video ($)", h_MAX_SPEND_PER_VIDEO: "The job stops cleanly if it would cost more. Empty = no limit.",
      f_CACHE_MAX_GB: "Cache size limit (GB)", h_CACHE_MAX_GB: "Above this size, the oldest cached files are deleted.",
      no_limit: "no limit", chip_estimate: "Estimated cost",
      estimate_tip: "Before cache savings. Analysis {a} · Script {s} · Voice {v}",
      savings_line: "This job: <b>{calls}</b> API calls · <b>{hits}</b> reused from cache",
      saved: "saved ≈ {m}",
      result_savings: "{calls} API calls · {hits} results reused from the cache",
    },
    fr: {
      tagline: "YouTube → MP4 doublé", spend_title: "Dépenses (estimation)", spend_job: "Ce travail", spend_month: "Ce mois",
      spend_all: "Depuis le début", role_analysis: "Analyse", role_script: "Script", role_voice: "Voix", total: "Total",
      nav_dub: "Doubler une vidéo", nav_settings: "Paramètres", quit: "Quitter",
      fake_banner: "Mode test hors ligne : IA simulée et vidéo de test générée. Aucun appel API, aucun coût réel.",
      new_title: "Nouveau doublage", url_label: "URL de la vidéo YouTube", lang_label: "Langue cible",
      review_label: "Relire le script avant la voix", review_hint: "Pause après la traduction pour modifier chaque phrase.",
      start: "DÉMARRER", cancel: "Annuler", pipeline_title: "Étapes",
      status_idle: "Prêt", status_running: "En cours", status_review: "En attente de vous", status_cancelling: "Annulation…",
      status_done: "Terminé", status_failed: "Échec", status_cancelled: "Annulé",
      progress_idle: "En attente d'une vidéo.", error_title: "Un problème est survenu", cancelled_title: "Travail annulé",
      open_settings: "Ouvrir les paramètres", close: "Fermer", final_ready: "VIDÉO FINALE PRÊTE", dl_mp4: "Télécharger le MP4",
      dl_srt: "Sous-titres (SRT)", dl_json: "Rapport de timing (JSON)", open_folder: "Ouvrir le dossier", saved_in: "Enregistré dans",
      quality_checks: "Contrôles qualité", new_video: "Nouvelle vidéo", review_title: "Relisez le script",
      review_text: "La traduction est prête. Modifiez les phrases dans le tableau ci-dessous, puis cliquez sur Continuer pour générer la voix.",
      continue: "Continuer", video_title: "Analyse de la vidéo", segments_title: "Parties parlées",
      segments_empty: "Les parties de la vidéo apparaissent ici après la transcription.",
      col_time: "Temps", col_slot: "Créneau", col_source: "Original", col_translation: "Traduction", col_voice: "Voix",
      col_speed: "Vitesse", col_mode: "Timing", log_title: "Journal en direct",
      keys_title: "Clés API OpenAI",
      keys_help: "Utilisez une clé différente pour chaque tâche pour voir et limiter ce que chacune dépense sur platform.openai.com. Laissez une clé vide pour utiliser la clé par défaut. Les clés restent sur cet ordinateur et ne sont envoyées qu'à OpenAI.",
      models_title: "Modèles IA et voix", model_analysis: "Modèle d'analyse (transcription)",
      model_script: "Modèle de script (traduction)", model_voice: "Modèle de voix (TTS)",
      analysis_model_note: "Le modèle d'analyse doit fournir les horodatages des segments (whisper-1 le fait).",
      voices_title: "Voix pour chaque langue", timing_title: "Timing", output_title: "Dossier de sortie",
      browse: "Parcourir...", open: "Ouvrir", output_hint: "Vide = le dossier « output » à côté de l'application.",
      prices_title: "Prix (estimations)",
      prices_help: "Servent uniquement à estimer les dépenses. Vérifiez sur openai.com/api/pricing et corrigez si besoin. En USD.",
      price_model: "Modèle", price_minute: "$ / minute audio", price_in: "$ / 1M jetons entrée",
      price_out: "$ / 1M jetons sortie", price_chars: "$ / 1M caractères", add_model: "+ Ajouter un modèle",
      counters_title: "Compteurs de dépenses",
      counters_help: "Les compteurs sont des estimations calculées sur cet ordinateur. Ils ne remplacent pas votre facture OpenAI.",
      reset_counters: "Remettre à zéro", discard: "Annuler les modifications", save: "Enregistrer",
      stopped_title: "L'application est arrêtée", stopped_text: "Vous pouvez fermer cette fenêtre. Pour relancer, double-cliquez sur run.bat.",
      lost_title: "Connexion perdue",
      stage_download: "Téléchargement", stage_probe: "Analyse vidéo", stage_extract: "Extraction audio",
      stage_transcribe: "Transcription", stage_translate: "Traduction", stage_voice: "Voix",
      stage_timing: "Ajustement du timing", stage_assemble: "Piste audio", stage_export: "Export MP4", stage_validate: "Contrôle qualité",
      sdesc_download: "Récupère la vidéo sur YouTube (meilleure qualité MP4, 1080p max).",
      sdesc_probe: "Lit la durée, la résolution, les images/seconde et les codecs.",
      sdesc_extract: "Extrait le son de la vidéo pour la reconnaissance vocale.",
      sdesc_transcribe: "Clé Analyse · trouve chaque phrase et le moment où elle est dite.",
      sdesc_translate: "Clé Script · traduit les phrases par lots, chacune avec son contexte.",
      sdesc_voice: "Clé Voix · lit à voix haute chaque phrase traduite.",
      sdesc_timing: "Fait tenir chaque phrase dans son temps : réécriture plus courte/longue, puis vitesse 0,82×–1,35×.",
      sdesc_assemble: "Place chaque phrase à son moment d'origine sur une piste audio.",
      sdesc_export: "Assemble l'image d'origine et la nouvelle voix ; écrit le SRT et le JSON.",
      sdesc_validate: "Vérifie que le MP4 s'ouvre, a du son, la bonne durée et aucun chevauchement.",
      st_waiting: "en attente", st_running: "en cours", st_done: "fait", st_failed: "échec",
      mode_normal: "normal", mode_concise_rewrite: "réécrit plus court", mode_speed_up: "accéléré",
      mode_expanded_rewrite: "réécrit plus long", mode_slow_down: "ralenti", mode_padded: "complété par un silence",
      seg_waiting: "en attente", seg_translated: "traduit", seg_voiced: "voix générée", legend_pause: "pause / temps libre",
      legend_pending: "pas terminé",
      chip_duration: "Durée", chip_resolution: "Résolution", chip_fps: "Images/s", chip_vcodec: "Codec vidéo",
      chip_audio: "Audio", chip_format: "Conteneur", no_audio: "aucun",
      key_analysis: "Clé Analyse", key_script: "Clé Script", key_voice: "Clé Voix", key_default: "Clé par défaut",
      keydesc_analysis: "Transcription : écoute la vidéo (Whisper).",
      keydesc_script: "Traduction et réécritures de longueur (GPT).",
      keydesc_voice: "Synthèse vocale : la nouvelle voix.",
      keydesc_default: "Facultative. Utilisée pour toute tâche dont la clé ci-dessus est vide.",
      show: "Afficher", hide: "Masquer", test_key: "Tester", testing: "Test…", remove: "Supprimer",
      src_settings: "Clé enregistrée : {m}", src_env: "Variable Windows {env} utilisée : {m}", src_default: "Clé par défaut utilisée : {m}",
      src_missing: "Pas encore de clé : collez-en une ici, ou renseignez la clé par défaut.", src_missing_default: "Non renseignée (facultatif).",
      ph_replace: "Collez une nouvelle clé pour la remplacer", ph_paste: "sk-...",
      f_MAX_SPEED_UP: "Accélération max", h_MAX_SPEED_UP: "Vitesse maximale d'une phrase (1,35 = 35 % plus vite).",
      f_MAX_SLOW_DOWN: "Ralentissement max", h_MAX_SLOW_DOWN: "Vitesse minimale d'une phrase (0,82 = 18 % plus lent).",
      f_SAFETY_GAP: "Marge de sécurité (s)", h_SAFETY_GAP: "Silence gardé avant la phrase suivante.",
      f_REWRITE_TRIES: "Essais de réécriture", h_REWRITE_TRIES: "Réécritures plus courtes / plus longues par phrase.",
      f_ORIGINAL_AUDIO_VOLUME: "Volume du son original", h_ORIGINAL_AUDIO_VOLUME: "Bande-son originale sous le doublage (0 = coupé, 0,15 = discret).",
      default_is: "par défaut {v}",
      saved_ok: "Paramètres enregistrés.", confirm_reset: "Remettre tous les compteurs de dépenses à zéro ? (Une copie de sauvegarde est gardée.)",
      confirm_quit: "Une vidéo est en cours de traitement. Quitter quand même ?", confirm_cancel: "Arrêter ce travail ?",
      reset_done: "Compteurs remis à zéro.", price_unknown: "prix inconnu", calls: "{n} appels API",
      elapsed: "Temps écoulé : {t}", segments_count: "{n} parties · {d} terminées", job_cost: "Coût de ce travail",
      sd_title: "Partie {n}", sd_start: "Début", sd_end: "Fin d'origine", sd_natural: "Temps parlé", sd_slot: "Créneau",
      sd_slot_end: "Fin du créneau", sd_tts: "Durée de la voix", sd_final: "Durée placée", sd_stretch: "Vitesse", sd_mode: "Timing",
      sd_attempts: "Appels API", sd_steps: "Étapes", sd_original: "Original", sd_translation: "Traduction",
      sd_play_original: "Voix originale", sd_play_dub: "Nouvelle voix", sd_no_audio: "Pas encore générée.",
      need_url: "Collez l'URL d'une vidéo YouTube.", folder_opened: "Dossier ouvert.", warnings: "Avertissements",
      key_ok_saved: "Clé supprimée.", none: "—",
      economy_title: "Économies",
      economy_help: "Ces options réduisent le coût de chaque vidéo sans différence notable sur le résultat.",
      economy_label: "Mode économie (ajuster la vitesse d'abord)",
      economy_hint: "Une phrase un peu trop longue ou trop courte est simplement accélérée ou ralentie. Elle n'est réécrite et revoicée que si le changement de vitesse serait trop fort. Désactivé = toujours réécrire d'abord.",
      cache_label: "Réutiliser ce qui a déjà été payé (cache)",
      cache_hint: "Les transcriptions, traductions et voix sont gardées sur cet ordinateur. Relancer une vidéo, ou la doubler dans une autre langue, ne paie pas deux fois.",
      clear_cache: "Vider le cache", cache_cleared: "Cache vidé.",
      confirm_clear_cache: "Supprimer toutes les transcriptions, traductions et voix en cache ? Relancer une vidéo coûtera alors à nouveau.",
      cache_info: "{size} utilisés · {n} résultats gardés", model_rewrite: "Modèle de réécriture (longueur)",
      model_rewrite_ph: "vide = le même que le modèle de script",
      f_ECONOMY_SPEEDUP_LIMIT: "Réécrire une phrase longue au-delà de", h_ECONOMY_SPEEDUP_LIMIT: "Mode économie : une phrase qui demande plus d'accélération que cela (1,15 = 15 % plus vite) est réécrite plus courte ; en dessous, elle est seulement accélérée.",
      f_ECONOMY_SLOWDOWN_LIMIT: "Réécrire une phrase courte en deçà de", h_ECONOMY_SLOWDOWN_LIMIT: "Mode économie : une phrase qui demande plus de ralentissement que cela pour atteindre sa durée minimale est réécrite plus longue ; au-dessus, elle est seulement ralentie.",
      f_MAX_SPEND_PER_VIDEO: "Dépense max par vidéo ($)", h_MAX_SPEND_PER_VIDEO: "Le travail s'arrête proprement s'il coûterait plus. Vide = pas de limite.",
      f_CACHE_MAX_GB: "Taille max du cache (Go)", h_CACHE_MAX_GB: "Au-delà, les fichiers les plus anciens sont supprimés.",
      no_limit: "pas de limite", chip_estimate: "Coût estimé",
      estimate_tip: "Avant économies du cache. Analyse {a} · Script {s} · Voix {v}",
      savings_line: "Ce travail : <b>{calls}</b> appels API · <b>{hits}</b> réutilisés du cache",
      saved: "économisé ≈ {m}",
      result_savings: "{calls} appels API · {hits} résultats réutilisés du cache",
    },
  };
  let LANG = "en";
  function t(key, vars) {
    let s = (I18N[LANG] && I18N[LANG][key]) ?? I18N.en[key] ?? key;
    if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
    return s;
  }

  // ------------------------------------------------------------------ state
  const S = {
    meta: null, job: null, spend: null, spendView: "job", settings: null, fake: false,
    clockOffset: 0, selected: null, reviewEdits: {}, quitting: false, renderedReview: false,
  };
  const serverNow = () => Date.now() / 1000 + S.clockOffset;
  const isRunning = () => !!(S.job && RUNNING.includes(S.job.status));

  async function api(method, path, body) {
    const opt = { method, headers: { "X-Dubbing": "1" } };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    const r = await fetch(path, opt);
    let data = {};
    try { data = await r.json(); } catch (_) { /* not JSON */ }
    if (!r.ok || data.ok === false) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }

  let toastTimer = null;
  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
  }

  // ------------------------------------------------------------------ formatting
  function fmtTime(s) {
    if (s === null || s === undefined) return "—";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    const ss = sec.toFixed(1).padStart(4, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
  }
  function fmtDur(s) {
    if (s === null || s === undefined || isNaN(s)) return "";
    if (s < 60) return `${s.toFixed(1)} s`;
    const m = Math.floor(s / 60), sec = Math.round(s % 60);
    return `${m} min ${String(sec).padStart(2, "0")} s`;
  }
  function money(c) {
    if (!c) return "$0.00";
    if (c < 0.01) return "$" + c.toFixed(4);
    if (c < 1) return "$" + c.toFixed(3);
    return "$" + c.toFixed(2);
  }
  function fmtCost(d) {
    if (!d || !d.calls) return "$0.00";
    if (d.unknown && !d.cost) return `<small>${esc(t("price_unknown"))}</small>`;
    if (d.unknown) return `${money(d.cost)} <small>+ ?</small>`;
    return money(d.cost);
  }
  const modeBadge = (m) => `<span class="badge m-${esc(m)}">${esc(t("mode_" + m))}</span>`;

  // ------------------------------------------------------------------ i18n apply
  function applyI18n() {
    document.documentElement.lang = LANG;
    $$("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
    $$("[data-i18n-ph]").forEach((el) => { el.placeholder = t(el.dataset.i18nPh); });
    $$(".lang-toggle button").forEach((b) => b.classList.toggle("active", b.dataset.lang === LANG));
    renderAll();
    if (S.settings) renderSettings();
  }

  // ------------------------------------------------------------------ spend
  function renderSpend() {
    const d = S.spend && S.spend[S.spendView];
    for (const role of [...ROLES, "total"]) {
      const el = $(`#spend-${role}`);
      const v = d ? d[role] : null;
      el.innerHTML = fmtCost(v);
      el.closest(".tile").title = v ? t("calls", { n: v.calls }) : "";
    }
    $$("#spend-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === S.spendView));
    // API calls / cache hits / money saved by the cache, for the current job
    const sv = S.spend && S.spend.savings;
    const box = $("#spend-savings");
    box.hidden = !sv || (!sv.api_calls && !sv.cache_hits);
    if (!box.hidden) {
      box.innerHTML = t("savings_line", { calls: sv.api_calls, hits: sv.cache_hits })
        + (sv.saved_usd ? ` · <span class="saved">${esc(t("saved", { m: money(sv.saved_usd) }))}</span>` : "");
    }
  }

  // ------------------------------------------------------------------ job rendering
  function renderAll() {
    renderSpend();
    renderStatus();
    renderStages();
    renderProgress();
    renderVideo();
    renderSegments();
    renderLog();
    renderResult();
  }

  function renderStatus() {
    const job = S.job;
    const status = job ? job.status : "idle";
    const pill = $("#job-status-pill");
    pill.dataset.status = status;
    pill.textContent = t("status_" + status);
    const running = isRunning();
    $("#start-btn").disabled = running;
    $("#cancel-btn").hidden = !running;
    $("#cancel-btn").disabled = status === "cancelling";
    $("#url").disabled = running;
    $("#language").disabled = running;
    $("#review-toggle").disabled = running;
    $("#review-card").hidden = status !== "review";

    const errCard = $("#error-card");
    if (job && (status === "failed" || status === "cancelled")) {
      errCard.hidden = false;
      $("#error-title").textContent = t(status === "cancelled" ? "cancelled_title" : "error_title");
      $("#error-msg").textContent = job.error || "";
      $("#error-settings-btn").hidden = status === "cancelled";
    } else {
      errCard.hidden = true;
    }
    // the table switches to editable text boxes during the review
    const inReview = status === "review";
    if (inReview !== S.renderedReview) renderSegments();
  }

  function renderStages() {
    const list = $("#stages");
    const byKey = {};
    if (S.job) S.job.stages.forEach((s) => { byKey[s.key] = s; });
    list.innerHTML = STAGES.map((key, n) => {
      const st = byKey[key] || { status: "waiting" };
      const icon = st.status === "done" ? "✓" : st.status === "failed" ? "✕" : String(n + 1);
      let time = "";
      if (st.status === "running" && st.started) time = `<span data-started="${st.started}">${fmtDur(serverNow() - st.started)}</span>`;
      else if (st.duration !== null && st.duration !== undefined) time = fmtDur(st.duration);
      const showMsg = st.message && (st.status === "running" || st.status === "failed");
      return `<li class="stage ${esc(st.status)}">
        <div class="stage-icon">${icon}</div>
        <div class="stage-name">${esc(t("stage_" + key))}<span class="stage-state">${esc(t("st_" + st.status))}</span></div>
        <div class="stage-time">${time}</div>
        <div class="stage-desc">${esc(t("sdesc_" + key))}</div>
        ${showMsg ? `<div class="stage-msg">${esc(st.message)}</div>` : ""}
      </li>`;
    }).join("");
  }

  function renderProgress() {
    const job = S.job;
    const pct = job ? job.progress || 0 : 0;
    $("#progress-bar").style.width = pct + "%";
    $("#progress-pct").textContent = Math.round(pct) + "%";
    $("#progress-msg").textContent = job ? job.message || "" : t("progress_idle");
    renderElapsed();
  }

  function renderElapsed() {
    const job = S.job;
    const el = $("#job-elapsed");
    if (!job || !job.started) { el.textContent = ""; return; }
    const end = job.ended || serverNow();
    el.textContent = t("elapsed", { t: fmtDur(end - job.started) });
  }

  function tick() {
    $$("#stages [data-started]").forEach((el) => {
      el.textContent = fmtDur(serverNow() - Number(el.dataset.started));
    });
    if (isRunning()) renderElapsed();
  }

  function containerName(fmt) {
    const names = String(fmt || "").split(",");           // ffprobe: "mov,mp4,m4a,3gp,3g2,mj2"
    const known = ["mp4", "webm", "matroska", "mov"].find((n) => names.includes(n));
    return (known || names[0] || "—").toUpperCase();
  }

  function renderVideo() {
    const v = S.job && S.job.video;
    $("#video-card").hidden = !v;
    if (!v) return;
    const audio = v.has_audio ? `${v.audio_codec} · ${Math.round(v.audio_sample_rate / 1000)} kHz · ${v.audio_channels} ch` : t("no_audio");
    const chips = [
      ["chip_duration", fmtTime(v.duration)], ["chip_resolution", `${v.width} × ${v.height}`],
      ["chip_fps", `${v.fps} fps`], ["chip_vcodec", `${v.video_codec} (${v.pix_fmt})`],
      ["chip_audio", audio], ["chip_format", containerName(v.format)],
    ];
    let html = chips.map(([k, val]) => `<div class="chip"><span>${esc(t(k))}</span><b>${esc(val)}</b></div>`).join("");
    const est = S.job.estimate;
    if (est) {
      const part = (x) => (x === null || x === undefined ? t("price_unknown") : money(x));
      const tip = t("estimate_tip", { a: part(est.analysis), s: part(est.script), v: part(est.voice) });
      html += `<div class="chip estimate" title="${esc(tip)}"><span>${esc(t("chip_estimate"))}</span><b>≈ ${esc(money(est.total))}${est.unknown && est.unknown.length ? " + ?" : ""}</b></div>`;
    }
    $("#video-chips").innerHTML = html;
  }

  // ------------------------------------------------------------------ segments
  function segStatusCell(r) {
    if (r.timing_mode) return modeBadge(r.timing_mode);
    return `<span class="badge st">${esc(t("seg_" + (r.status || "waiting")))}</span>`;
  }

  function segRowHtml(r, review) {
    const tr = r.translated_text;
    let trCell;
    if (review) {
      const val = S.reviewEdits[r.i] ?? tr ?? "";
      trCell = `<td class="text"><textarea data-i="${r.i}" rows="2">${esc(val)}</textarea></td>`;
    } else {
      trCell = `<td class="text ${tr ? "" : "pending"}">${tr ? esc(tr) : "…"}</td>`;
    }
    const voice = r.tts_duration !== null && r.tts_duration !== undefined
      ? `${num(r.tts_duration)} s${r.final_duration !== null && r.final_duration !== undefined ? `<small>→ ${num(r.final_duration)} s</small>` : ""}`
      : "—";
    const speed = r.stretch_factor ? `×${num(r.stretch_factor)}` : "—";
    const warn = r.warnings && r.warnings.length ? `<span class="warn-icon" title="${esc(r.warnings.join("\n"))}">⚠</span>` : "";
    return `<tr data-i="${r.i}" class="${S.selected === r.i ? "selected" : ""}">
      <td class="num">${r.index}</td>
      <td class="time">${fmtTime(r.start)} → ${fmtTime(r.end)}<small>${num(r.natural)} s</small></td>
      <td class="time">${num(r.slot)} s</td>
      <td class="text">${esc(r.source_text)}</td>
      ${trCell}
      <td class="time">${voice}</td>
      <td class="time">${speed}</td>
      <td>${segStatusCell(r)}</td>
      <td>${warn}</td>
    </tr>`;
  }

  function renderSegments() {
    const segs = (S.job && S.job.segments) || [];
    const review = !!(S.job && S.job.status === "review");
    S.renderedReview = review;
    $("#segments-empty").hidden = segs.length > 0;
    $("#segments-body").hidden = segs.length === 0;
    renderSegCount();
    if (!segs.length) { $("#seg-tbody").innerHTML = ""; return; }
    $("#seg-tbody").innerHTML = segs.map((r) => segRowHtml(r, review)).join("");
    renderTimeline();
    renderLegend();
  }

  function renderSegCount() {
    const segs = (S.job && S.job.segments) || [];
    $("#segments-count").textContent = segs.length
      ? t("segments_count", { n: segs.length, d: segs.filter((s) => s.status === "done").length }) : "";
  }

  function updateSegmentRow(r) {
    if (S.renderedReview) return;           // never overwrite what the user is typing
    const old = $(`#seg-tbody tr[data-i="${r.i}"]`);
    if (!old) { renderSegments(); return; }
    const tmp = document.createElement("tbody");
    tmp.innerHTML = segRowHtml(r, false);
    old.replaceWith(tmp.firstElementChild);
    if (S.selected === r.i && $("#seg-dialog").open) openSegment(r.i, true);
  }

  let tlPending = false;
  function scheduleTimeline() {
    if (tlPending) return;
    tlPending = true;
    requestAnimationFrame(() => { tlPending = false; renderTimeline(); });
  }

  function renderTimeline() {
    const segs = (S.job && S.job.segments) || [];
    if (!segs.length) return;
    const D = (S.job.video && S.job.video.duration) || Math.max(...segs.map((s) => s.slot_end || s.end));
    const pct = (x) => `${Math.max(0, Math.min(100, (x / D) * 100))}%`;
    let html = "";
    for (const s of segs) {
      html += `<div class="tl-slot" style="left:${pct(s.start)};width:${pct(s.slot_end - s.start)}"></div>`;
    }
    for (const s of segs) {
      const cls = s.timing_mode ? "c-" + s.timing_mode : "c-pending";
      const voiceW = s.final_duration ? (s.final_duration / Math.max(0.01, s.end - s.start)) * 100 : 0;
      const tip = `#${s.index}  ${fmtTime(s.start)} → ${fmtTime(s.end)}${s.timing_mode ? "  ·  " + t("mode_" + s.timing_mode) : ""}`;
      html += `<div class="tl-seg ${cls} ${S.selected === s.i ? "selected" : ""}" data-i="${s.i}" title="${esc(tip)}"
        style="left:${pct(s.start)};width:${pct(s.end - s.start)}">${voiceW ? `<div class="tl-voice" style="width:${voiceW}%"></div>` : ""}</div>`;
    }
    $("#timeline").innerHTML = html;
    const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
    const step = steps.find((x) => D / x <= 8) || 3600;
    let axis = "";
    for (let x = 0; x <= D + 1e-6; x += step) axis += `<span style="left:${pct(x)}">${fmtTime(x).replace(/\.\d$/, "")}</span>`;
    $("#timeline-axis").innerHTML = axis;
  }

  function renderLegend() {
    $("#legend").innerHTML = MODES.map((m) => `<span><i class="c-${m}"></i>${esc(t("mode_" + m))}</span>`).join("")
      + `<span><i class="c-pending"></i>${esc(t("legend_pending"))}</span>`
      + `<span><i style="background:repeating-linear-gradient(45deg,var(--surface-2),var(--surface-2) 2px,var(--border-strong) 2px,var(--border-strong) 4px)"></i>${esc(t("legend_pause"))}</span>`;
  }

  function selectSegment(i) {
    S.selected = i;
    $$("#seg-tbody tr").forEach((tr) => tr.classList.toggle("selected", Number(tr.dataset.i) === i));
    $$("#timeline .tl-seg").forEach((el) => el.classList.toggle("selected", Number(el.dataset.i) === i));
  }

  function openSegment(i, refresh) {
    const r = S.job && S.job.segments[i];
    if (!r) return;
    selectSegment(i);
    const kv = [
      ["sd_start", fmtTime(r.start)], ["sd_end", fmtTime(r.end)], ["sd_natural", `${num(r.natural)} s`],
      ["sd_slot", `${num(r.slot)} s`], ["sd_slot_end", fmtTime(r.slot_end)],
      ["sd_tts", r.tts_duration !== null && r.tts_duration !== undefined ? `${num(r.tts_duration)} s` : "—"],
      ["sd_final", r.final_duration !== null && r.final_duration !== undefined ? `${num(r.final_duration)} s` : "—"],
      ["sd_stretch", r.stretch_factor ? `×${num(r.stretch_factor, 3)}` : "—"],
      ["sd_attempts", r.attempts ?? "—"],
    ];
    const v = encodeURIComponent(S.job.id);
    const dubAudio = r.has_audio
      ? `<audio controls preload="none" src="/api/job/segment/${i}/audio?v=${v}"></audio>`
      : `<div class="muted small">${esc(t("sd_no_audio"))}</div>`;
    const origAudio = S.job.video
      ? `<audio controls preload="none" src="/api/job/segment/${i}/audio?which=original&v=${v}"></audio>` : "";
    $("#sd-title").textContent = t("sd_title", { n: r.index });
    $("#sd-body").innerHTML = `
      <div class="kv">${kv.map(([k, val]) => `<div><span>${esc(t(k))}</span><b>${esc(val)}</b></div>`).join("")}
        <div><span>${esc(t("sd_mode"))}</span>${r.timing_mode ? modeBadge(r.timing_mode) : `<span class="badge st">${esc(t("seg_" + (r.status || "waiting")))}</span>`}</div></div>
      ${r.steps && r.steps.length ? `<div class="kv"><div style="grid-column:1/-1"><span>${esc(t("sd_steps"))}</span>${r.steps.map(modeBadge).join(" ")}</div></div>` : ""}
      ${r.warnings && r.warnings.length ? `<div class="warn-box">⚠ ${r.warnings.map(esc).join("<br>")}</div>` : ""}
      <div class="text-block"><span>${esc(t("sd_original"))}</span>${esc(r.source_text)}</div>
      <div class="text-block"><span>${esc(t("sd_translation"))}</span>${esc(r.translated_text || "…")}</div>
      <div class="audio-row">
        <div><label>${esc(t("sd_play_original"))}</label>${origAudio}</div>
        <div><label>${esc(t("sd_play_dub"))}</label>${dubAudio}</div>
      </div>`;
    const dlg = $("#seg-dialog");
    if (!refresh && !dlg.open) dlg.showModal();
  }

  // ------------------------------------------------------------------ result / log
  function renderResult() {
    const job = S.job;
    const card = $("#result-card");
    const res = job && job.status === "done" ? job.result : null;
    if (!res) {
      if (!card.hidden) { $("#result-video").removeAttribute("src"); $("#result-video").load(); }
      card.hidden = true;
      return;
    }
    const v = encodeURIComponent(job.id);
    card.hidden = false;
    $("#result-title").textContent = res.title;
    $("#dl-mp4").href = `/api/job/file/mp4?v=${v}`;
    $("#dl-mp4").setAttribute("download", res.video_name);
    $("#dl-srt").href = `/api/job/file/srt?v=${v}`;
    $("#dl-srt").setAttribute("download", res.srt_name);
    $("#dl-json").href = `/api/job/file/json?v=${v}`;
    $("#dl-json").setAttribute("download", res.json_name);
    const video = $("#result-video");
    const src = `/api/job/file/mp4?inline=1&v=${v}`;
    if (video.getAttribute("src") !== src) video.setAttribute("src", src);
    $("#result-path").textContent = res.video_path;
    const cost = res.cost && res.cost.total;
    $("#result-cost").innerHTML = cost ? `${esc(t("job_cost"))}<b>${fmtCost(cost)}</b>` : "";
    const sv = res.cost || null;
    const savings = $("#result-savings");
    savings.hidden = !sv || sv.api_calls === undefined;
    if (!savings.hidden) {
      savings.innerHTML = esc(t("result_savings", { calls: sv.api_calls, hits: sv.cache_hits }))
        + (sv.saved_usd ? ` · <span class="saved">${esc(t("saved", { m: money(sv.saved_usd) }))}</span>` : "");
    }
    const items = (res.checks || []).map((c) => `<li class="ok">${esc(c)}</li>`)
      .concat((res.warnings || []).map((w) => `<li class="warn">${esc(w)}</li>`));
    $("#checks-list").innerHTML = items.join("");
    $("#checks-count").textContent = `(${(res.checks || []).length} ✓${res.warnings && res.warnings.length ? ` · ${res.warnings.length} ⚠` : ""})`;
  }

  function logLineHtml(e) {
    const d = new Date(e.t * 1000);
    const time = d.toTimeString().slice(0, 8);
    return `<span class="l-${esc(e.level)}"><span class="l-time">${time}</span>  ${esc(e.line)}</span>\n`;
  }
  function renderLog() {
    const logs = (S.job && S.job.logs) || [];
    $("#log").innerHTML = logs.map(logLineHtml).join("");
    $("#log-count").textContent = logs.length ? `(${logs.length})` : "";
    const el = $("#log");
    el.scrollTop = el.scrollHeight;
  }
  function appendLog(e) {
    const el = $("#log");
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    el.insertAdjacentHTML("beforeend", logLineHtml(e));
    while (el.childNodes.length > 1300) el.removeChild(el.firstChild);
    $("#log-count").textContent = `(${S.job.logs.length})`;
    if (atBottom) el.scrollTop = el.scrollHeight;
  }

  // ------------------------------------------------------------------ live events
  let es = null;
  let lostTimer = null;
  function on(kind, fn) {
    es.addEventListener(kind, (e) => {
      try { fn(JSON.parse(e.data)); } catch (err) { console.error(kind, err); }
    });
  }
  function connect() {
    es = new EventSource("/api/events");
    on("snapshot", (d) => {
      S.clockOffset = d.server_time - Date.now() / 1000;
      S.job = d.job;
      S.spend = d.spend;
      S.fake = d.fake;
      $("#fake-banner").hidden = !d.fake;
      clearTimeout(lostTimer);
      lostTimer = null;
      $("#offline-overlay").hidden = true;
      renderAll();
    });
    on("job", (d) => {
      S.job = d;
      S.selected = null;
      S.reviewEdits = {};
      renderAll();
    });
    on("stage", (st) => {
      if (!S.job) return;
      const i = S.job.stages.findIndex((s) => s.key === st.key);
      if (i >= 0) S.job.stages[i] = st;
      renderStages();
    });
    on("progress", (d) => {
      if (!S.job) return;
      S.job.progress = d.progress;
      S.job.message = d.message;
      renderProgress();
    });
    on("log", (e) => {
      if (!S.job) return;
      S.job.logs.push(e);
      appendLog(e);
    });
    on("video", (v) => {
      if (!S.job) return;
      S.job.video = v;
      renderVideo();
    });
    on("segments", (d) => {
      if (!S.job) return;
      S.job.segments = d.segments;
      renderSegments();
    });
    on("segment", (r) => {
      if (!S.job || !S.job.segments) return;
      S.job.segments[r.i] = r;
      updateSegmentRow(r);
      scheduleTimeline();
      renderSegCount();
    });
    on("status", (d) => {
      if (!S.job) return;
      S.job.status = d.status;
      S.job.error = d.error;
      if (d.result) S.job.result = d.result;
      if (d.ended) S.job.ended = d.ended;
      renderStatus();
      renderResult();
      renderElapsed();
    });
    on("spend", (d) => {
      S.spend = d;
      renderSpend();
    });
    on("estimate", (d) => {
      if (!S.job) return;
      S.job.estimate = d;
      renderVideo();
    });
    es.onerror = () => {
      if (S.quitting) { es.close(); return; }
      if (!lostTimer) {
        lostTimer = setTimeout(() => {
          $("#offline-title").textContent = t("lost_title");
          $("#offline-overlay").hidden = false;
        }, 6000);
      }
    };
  }

  // ------------------------------------------------------------------ job actions
  async function startJob() {
    const errEl = $("#start-error");
    errEl.hidden = true;
    const url = $("#url").value.trim();
    if (!url) { errEl.textContent = t("need_url"); errEl.hidden = false; $("#url").focus(); return; }
    $("#start-btn").disabled = true;
    try {
      await api("POST", "/api/job", { url, language: $("#language").value, review: $("#review-toggle").checked });
    } catch (e) {
      errEl.textContent = e.message;
      errEl.hidden = false;
      $("#start-btn").disabled = isRunning();
    }
  }

  // ------------------------------------------------------------------ settings
  async function loadSettings() {
    try {
      S.settings = await api("GET", "/api/settings");
      renderSettings();
      $("#save-msg").textContent = "";
    } catch (e) {
      $("#save-msg").textContent = e.message;
      $("#save-msg").className = "err";
    }
  }

  function keySourceText(role) {
    const src = S.settings.key_sources[role];
    if (src.source === "settings") return [t("src_settings", { m: src.masked }), "ok"];
    if (src.source === "default key") return [t("src_default", { m: src.masked }), "ok"];
    if (src.source === "missing") return [t(role === "default" ? "src_missing_default" : "src_missing"), role === "default" ? "" : "missing"];
    return [t("src_env", { env: src.source, m: src.masked }), "ok"];
  }

  function renderKeys() {
    $("#keys").innerHTML = KEY_ROLES.map((role) => {
      const saved = !!S.settings.keys[role];
      const [srcText, srcCls] = keySourceText(role);
      return `<div class="key-row" data-role="${role}">
        <div>
          <div class="key-title">${role !== "default" ? `<span class="dot role-${role}"></span>` : ""}${esc(t("key_" + role))}</div>
          <div class="key-desc">${esc(t("keydesc_" + role))}</div>
        </div>
        <div>
          <div class="key-inputs">
            <input type="password" autocomplete="off" spellcheck="false" placeholder="${esc(saved ? t("ph_replace") : t("ph_paste"))}">
            <button class="btn btn-sm" data-act="show">${esc(t("show"))}</button>
            <button class="btn btn-sm" data-act="test">${esc(t("test_key"))}</button>
            ${saved ? `<button class="btn btn-sm btn-ghost" data-act="clear">${esc(t("remove"))}</button>` : ""}
          </div>
          <div class="key-src ${srcCls}">${esc(srcText)}</div>
          <div class="key-msg"></div>
        </div>
      </div>`;
    }).join("");
  }

  function renderSettings() {
    const s = S.settings;
    if (!s) return;
    renderKeys();
    $("#model-analysis").value = s.models.analysis;
    $("#model-script").value = s.models.script;
    $("#model-voice").value = s.models.voice;
    $("#voices").innerHTML = (S.meta.languages || []).map((lang) => `<label>${esc(lang)}
        <select data-lang="${esc(lang)}">${s.voices_available.map((v) => `<option ${s.voices[lang] === v ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>`).join("");
    $("#timing-fields").innerHTML = NUM_FIELDS.map(([name, step]) => {
      const [lo, hi] = s.limits[name];
      return `<label class="field"><span>${esc(t("f_" + name))} <span class="hint">(${esc(t("default_is", { v: s.defaults[name] }))})</span></span>
        <input type="number" data-num="${name}" min="${lo}" max="${hi}" step="${step}" value="${esc(s[name])}">
        <span class="hint">${esc(t("h_" + name))}</span></label>`;
    }).join("");
    $("#model-rewrite").value = s.models.rewrite || "";
    $("#economy-toggle").checked = !!s.ECONOMY_MODE;
    $("#cache-toggle").checked = !!s.CACHE_ENABLED;
    $("#economy-fields").innerHTML = ECON_FIELDS.map(([name, step]) => {
      const [lo, hi] = s.limits[name];
      const def = s.defaults[name] === null || s.defaults[name] === undefined ? t("no_limit") : s.defaults[name];
      const optional = name === "MAX_SPEND_PER_VIDEO";
      return `<label class="field"><span>${esc(t("f_" + name))} <span class="hint">(${esc(t("default_is", { v: def }))})</span></span>
        <input type="number" data-econ="${name}" min="${lo}" max="${hi}" step="${step}" value="${esc(s[name] ?? "")}"${optional ? ` placeholder="${esc(t("no_limit"))}"` : ""}>
        <span class="hint">${esc(t("h_" + name))}</span></label>`;
    }).join("");
    loadCacheInfo();
    $("#output-dir").value = s.output_dir || "";
    $("#output-dir").placeholder = s.defaults.output_dir;
    $("#price-tbody").innerHTML = Object.entries(s.pricing_effective).map(([m, p]) => priceRow(m, p)).join("");
    $("#settings-path").textContent = s.paths.settings;
  }

  function fmtBytes(b) {
    if (!b) return "0 MB";
    if (b < 1e9) return `${(b / 1e6).toFixed(b < 1e7 ? 1 : 0)} MB`;
    return `${(b / 1e9).toFixed(2)} GB`;
  }

  async function loadCacheInfo() {
    try {
      const c = await api("GET", "/api/cache");
      $("#cache-info").textContent = t("cache_info", { size: fmtBytes(c.bytes), n: c.entries });
      $("#cache-info").title = c.path;
    } catch (e) {
      $("#cache-info").textContent = e.message;
    }
  }

  function priceRow(model, p) {
    p = p || {};
    const cell = (f) => `<td><input type="number" min="0" step="any" data-f="${f}" value="${p[f] ?? ""}"></td>`;
    return `<tr><td><input data-f="model" value="${esc(model)}" spellcheck="false"></td>${S.settings.price_fields.map(cell).join("")}
      <td><button class="icon-btn" data-act="del-price" title="Remove">✕</button></td></tr>`;
  }

  function collectSettings() {
    const body = { keys: {}, models: {}, voices: {}, pricing: {} };
    $$("#keys .key-row").forEach((row) => {
      const v = $("input", row).value.trim();
      if (v) body.keys[row.dataset.role] = v;
    });
    body.models = {
      analysis: $("#model-analysis").value.trim(), script: $("#model-script").value.trim(),
      voice: $("#model-voice").value.trim(), rewrite: $("#model-rewrite").value.trim(),
    };
    body.ECONOMY_MODE = $("#economy-toggle").checked;
    body.CACHE_ENABLED = $("#cache-toggle").checked;
    $$("#economy-fields input").forEach((inp) => { body[inp.dataset.econ] = inp.value.trim(); });
    $$("#voices select").forEach((sel) => { body.voices[sel.dataset.lang] = sel.value; });
    $$("#timing-fields input").forEach((inp) => { body[inp.dataset.num] = inp.value; });
    body.output_dir = $("#output-dir").value.trim();
    $$("#price-tbody tr").forEach((tr) => {
      const model = $('input[data-f="model"]', tr).value.trim();
      if (!model) return;
      const entry = {};
      $$("input[type=number]", tr).forEach((inp) => { if (inp.value !== "") entry[inp.dataset.f] = inp.value; });
      body.pricing[model] = entry;
    });
    return body;
  }

  async function saveSettings() {
    const msg = $("#save-msg");
    try {
      const res = await api("POST", "/api/settings", collectSettings());
      S.settings = res.settings;
      renderSettings();
      msg.textContent = t("saved_ok");
      msg.className = "ok";
    } catch (e) {
      msg.textContent = e.message;
      msg.className = "err";
    }
  }

  async function keyAction(btn) {
    const row = btn.closest(".key-row");
    const role = row.dataset.role;
    const input = $("input", row);
    const msg = $(".key-msg", row);
    const act = btn.dataset.act;
    if (act === "show") {
      const hidden = input.type === "password";
      input.type = hidden ? "text" : "password";
      btn.textContent = t(hidden ? "hide" : "show");
    } else if (act === "test") {
      msg.className = "key-msg";
      msg.textContent = t("testing");
      btn.disabled = true;
      try {
        const res = await api("POST", "/api/settings/test-key", { role, key: input.value.trim() });
        msg.className = "key-msg ok";
        msg.textContent = "✓ " + res.message;
      } catch (e) {
        msg.className = "key-msg err";
        msg.textContent = "✕ " + e.message;
      } finally {
        btn.disabled = false;
      }
    } else if (act === "clear") {
      try {
        const res = await api("POST", "/api/settings", { clear_keys: [role] });
        S.settings.keys = res.settings.keys;
        S.settings.key_sources = res.settings.key_sources;
        renderKeys();
        toast(t("key_ok_saved"));
      } catch (e) {
        msg.className = "key-msg err";
        msg.textContent = e.message;
      }
    }
  }

  // ------------------------------------------------------------------ navigation
  function showTab(tab) {
    $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    $("#view-dub").hidden = tab !== "dub";
    $("#view-settings").hidden = tab !== "settings";
    if (tab === "settings") loadSettings();
    try { history.replaceState(null, "", tab === "settings" ? "#settings" : "#"); } catch (_) { /* ignore */ }
    window.scrollTo(0, 0);
  }

  async function quit() {
    if (isRunning() && !confirm(t("confirm_quit"))) return;
    S.quitting = true;
    try { await api("POST", "/api/quit"); } catch (_) { /* already stopped */ }
    if (es) es.close();
    $("#offline-title").textContent = t("stopped_title");
    $("#offline-overlay").hidden = false;
    setTimeout(() => window.close(), 600);
  }

  // ------------------------------------------------------------------ wiring
  function wire() {
    $$(".nav-btn").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
    $$("#spend-tabs button").forEach((b) => b.addEventListener("click", () => { S.spendView = b.dataset.view; renderSpend(); }));
    $$(".lang-toggle button").forEach((b) => b.addEventListener("click", () => {
      LANG = b.dataset.lang;
      applyI18n();
      api("POST", "/api/settings", { ui_lang: LANG }).catch(() => {});
    }));
    $("#quit-btn").addEventListener("click", quit);
    $("#start-btn").addEventListener("click", startJob);
    $("#url").addEventListener("keydown", (e) => { if (e.key === "Enter") startJob(); });
    $("#cancel-btn").addEventListener("click", async () => {
      if (!confirm(t("confirm_cancel"))) return;
      try { await api("POST", "/api/job/cancel"); } catch (e) { toast(e.message); }
    });
    $("#review-continue").addEventListener("click", async () => {
      const btn = $("#review-continue");
      btn.disabled = true;
      try {
        await api("POST", "/api/job/review", { translations: S.reviewEdits });
        S.reviewEdits = {};
      } catch (e) { toast(e.message); } finally { btn.disabled = false; }
    });
    $("#seg-tbody").addEventListener("input", (e) => {
      if (e.target.matches("textarea[data-i]")) S.reviewEdits[e.target.dataset.i] = e.target.value;
    });
    $("#seg-tbody").addEventListener("click", (e) => {
      if (e.target.closest("textarea")) return;
      const tr = e.target.closest("tr[data-i]");
      if (tr) openSegment(Number(tr.dataset.i));
    });
    $("#timeline").addEventListener("click", (e) => {
      const el = e.target.closest(".tl-seg");
      if (!el) return;
      const i = Number(el.dataset.i);
      if (S.renderedReview) {
        selectSegment(i);
        const tr = $(`#seg-tbody tr[data-i="${i}"]`);
        if (tr) tr.scrollIntoView({ block: "center", behavior: "smooth" });
      } else {
        openSegment(i);
      }
    });
    $("#sd-close").addEventListener("click", () => $("#seg-dialog").close());
    $("#seg-dialog").addEventListener("click", (e) => { if (e.target.id === "seg-dialog") $("#seg-dialog").close(); });
    $("#seg-dialog").addEventListener("close", () => $$("#seg-dialog audio").forEach((a) => a.pause()));
    $("#open-folder-btn").addEventListener("click", async () => {
      try { await api("POST", "/api/job/open-folder"); toast(t("folder_opened")); } catch (e) { toast(e.message); }
    });
    const closeJob = async () => {
      try { await api("DELETE", "/api/job"); } catch (e) { toast(e.message); }
      $("#url").focus();
    };
    $("#new-job-btn").addEventListener("click", closeJob);
    $("#error-close-btn").addEventListener("click", closeJob);
    $("#error-settings-btn").addEventListener("click", () => showTab("settings"));

    // settings
    $("#keys").addEventListener("click", (e) => { const b = e.target.closest("button[data-act]"); if (b) keyAction(b); });
    $("#save-settings").addEventListener("click", saveSettings);
    $("#reload-settings").addEventListener("click", loadSettings);
    $("#add-price").addEventListener("click", () => {
      $("#price-tbody").insertAdjacentHTML("beforeend", priceRow("", {}));
      const inputs = $$("#price-tbody tr:last-child input");
      if (inputs.length) inputs[0].focus();
    });
    $("#price-tbody").addEventListener("click", (e) => {
      const b = e.target.closest('[data-act="del-price"]');
      if (b) b.closest("tr").remove();
    });
    $("#browse-output").addEventListener("click", async () => {
      try {
        const res = await api("POST", "/api/settings/browse-output", { current: $("#output-dir").value || $("#output-dir").placeholder });
        if (res.path) $("#output-dir").value = res.path;
      } catch (e) { toast(e.message); }
    });
    $("#open-output").addEventListener("click", async () => {
      try { await api("POST", "/api/settings/open-output"); } catch (e) { toast(e.message); }
    });
    $("#clear-cache").addEventListener("click", async () => {
      if (!confirm(t("confirm_clear_cache"))) return;
      try {
        await api("POST", "/api/cache/clear");
        toast(t("cache_cleared"));
        loadCacheInfo();
      } catch (e) { toast(e.message); }
    });
    $("#reset-spend").addEventListener("click", async () => {
      if (!confirm(t("confirm_reset"))) return;
      try {
        const res = await api("POST", "/api/spend/reset");
        S.spend = res.spend;
        renderSpend();
        toast(t("reset_done"));
      } catch (e) { toast(e.message); }
    });
  }

  async function init() {
    wire();
    try {
      S.meta = await api("GET", "/api/meta");
    } catch (e) {
      $("#offline-overlay").hidden = false;
      return;
    }
    LANG = I18N[S.meta.ui_lang] ? S.meta.ui_lang : "en";
    $("#language").innerHTML = S.meta.languages.map((l) => `<option ${l === S.meta.default_language ? "selected" : ""}>${esc(l)}</option>`).join("");
    $("#fake-banner").hidden = !S.meta.fake;
    applyI18n();
    connect();
    setInterval(tick, 1000);
    if (location.hash === "#settings") showTab("settings");
    else $("#url").focus();
  }

  init();
})();
