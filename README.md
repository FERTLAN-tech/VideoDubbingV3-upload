# Video Dubbing V3

Paste a YouTube URL, pick a language, press **START**: you get a finished MP4 with a
synchronized translated voice-over, plus subtitles and a timing report. The app shows
every step, every spoken part of the video, and how much each OpenAI API spent.

## Démarrage rapide (FR)
1. Double-cliquez **install.bat** (installe Python, FFmpeg, Deno et les paquets).
2. Double-cliquez **run.bat** : l'application s'ouvre dans une fenêtre (Microsoft Edge).
   Gardez la fenêtre noire ouverte ; la fermer arrête l'application.
3. La première fois : cliquez **Paramètres** et collez votre clé OpenAI.
   Vous pouvez mettre **une clé différente pour chaque tâche** : **Analyse** (transcription),
   **Script** (traduction) et **Voix** (synthèse vocale), ou une seule **clé par défaut** pour tout.
   Le bouton **Tester** vérifie une clé sans rien dépenser. Cliquez **Enregistrer**.
4. Revenez sur **Doubler une vidéo**, collez l'URL YouTube, choisissez la langue, cliquez **DÉMARRER**.
5. Vous voyez chaque étape (téléchargement, analyse, transcription, traduction, voix, timing,
   export, contrôle) et chaque partie parlée de la vidéo, avec sa traduction et son timing.
   Cliquez sur une partie pour écouter la voix originale et la nouvelle voix.
6. À la fin : **Télécharger le MP4**, les sous-titres, le rapport, ou **Ouvrir le dossier**.

En haut, les **dépenses** de chaque API (Analyse, Script, Voix et Total) s'affichent en
direct : pour le travail en cours, ce mois-ci ou depuis le début. Ce sont des estimations.
Le bouton **EN / FR** change la langue de l'interface. **Quitter** arrête l'application.

Option : **Relire le script avant la voix** met le travail en pause après la traduction pour
que vous puissiez corriger chaque phrase, puis **Continuer**.

**Économies** (Paramètres → *Économies*) : le **mode économie** (activé par défaut) accélère ou
ralentit légèrement une phrase au lieu de la faire réécrire et revoicer. Tout ce qui a été payé
(transcription, traduction, voix) est gardé dans un **cache** : relancer une vidéo, ou la doubler
dans une autre langue, ne paie pas deux fois (bouton **Vider le cache**). **Dépense max par vidéo**
arrête proprement un travail qui coûterait plus. Le coût estimé s'affiche dès que la vidéo est
analysée, et le bandeau des dépenses indique les appels API, les réutilisations du cache et
l'argent économisé.

## Quick start
1. Double-click **install.bat**. It installs, only if missing: Python 3.12, FFmpeg and Deno
   (needed by the YouTube downloader) via `winget`, then the Python packages in a private
   `.venv` folder.
2. Double-click **run.bat**. The app opens in its own window (Microsoft Edge app mode, or your
   default browser). Keep the black console window open: closing it stops the app.
3. First time: open **Settings** and paste your OpenAI API key(s), then **Save settings**.
4. Paste the URL, choose the target language, click **START**.
5. When it says **FINAL VIDEO READY**: **Download MP4**, the SRT, the timing JSON, or
   **Open output folder**.

**Downloads stopped working?** YouTube changes often: double-click **update.bat**.

## The interface
A small local web server (`app.py`, Flask) serves a single page (`static\`). It only listens
on `127.0.0.1`, so nothing is reachable from other computers.

* **Pipeline**: every stage with its status (waiting / running / done / failed), duration and
  a short explanation: Download → Analyse video → Extract audio → Transcribe → Translate →
  Voice → Timing fit → Build audio → Export MP4 → Quality check. Overall progress bar,
  elapsed time, and a collapsible live log.
* **Video analysis**: duration, resolution, frame rate, codecs and audio of the source.
* **Speech segments**: each spoken part as soon as it is found: time codes, slot, original
  text, translation, voice length, speed factor and timing mode (colour badges: normal,
  shorter rewrite, sped up, fuller rewrite, slowed down, padded) and warnings. A timeline
  shows the segments and the pauses over the whole video. Click a segment for details and
  to play the original voice and the new voice.
* **Review step** (optional): pause after translation, edit any line, then **Continue**.
* **Cancel** stops the job cleanly between steps / segments. One job runs at a time.
* Temporary files (downloaded video, voice clips) are kept while a job is shown, so clips can
  be played, and deleted when you click **New video**, start another job or quit.

The app stops when you click **Quit**, when you close the console window, or 3 minutes after
the last app window was closed if no video is being processed.
`run.bat --no-browser` starts the server without opening a window.

## API keys: one per job
| Key | Used for | Environment variable fallback |
|---|---|---|
| **Analysis** | transcription (Whisper) | `OPENAI_API_KEY_ANALYSIS` |
| **Script** | translation + length rewrites (GPT) | `OPENAI_API_KEY_SCRIPT` |
| **Voice** | text-to-speech | `OPENAI_API_KEY_VOICE` |
| **Default** (optional) | any job whose key is empty | `OPENAI_API_KEY` |

Lookup order for a job: its key in Settings → its environment variable → the default key in
Settings → `OPENAI_API_KEY`. Using separate keys lets you see and cap each job's spending on
platform.openai.com.

Keys are saved in `%APPDATA%\VideoDubbingV3\settings.json` (never in the project folder, so
they cannot end up in git). They are only sent to OpenAI, are masked in logs (`sk-...abcd`),
and the interface only ever receives masked keys: a key is sent from the page only when you
type a new one.

## Spend tracking
Every API call is recorded with its usage and an **estimated** price:

* Analysis: minutes of audio sent for transcription
* Script: input / output tokens reported by the API
* Voice: input characters (≈ tokens = characters / 4) and minutes of audio generated

The header shows Analysis, Script, Voice and Total for **this job**, **this month** and
**all time**, updated live. Each job's cost is also written in its timing JSON
(`cost_estimate_usd`). The price table is in `dubbing\config.py` (`PRICING`) and can be
edited in **Settings → Prices**. A model missing from the table shows "price unknown" instead
of a wrong number. These are estimates, not your invoice.

The ledger is `%APPDATA%\VideoDubbingV3\usage.jsonl` (one JSON line per call: time, job id,
role, model, units, cost). **Settings → Reset counters** clears it (a backup copy is kept).

## Saving money
Everything below is on by default and does not noticeably change the result.

* **Cache.** Every paid result is kept in `%APPDATA%\VideoDubbingV3\cache\`: the transcript
  (per YouTube video id, or audio hash), each translation, each length rewrite and each voice
  clip. The key is a hash of everything that changes the result (text, context, language,
  model, voice, style, prompt version), so a wrong result is never reused. Re-running a video,
  retrying after an error, or dubbing the same video into **another language** (no new
  transcription) costs nothing for what was already done. Cache hits are counted as hits,
  never as spend. Size cap: 2 GB by default (oldest files deleted first). **Settings → Saving
  money** shows its size and has a **Clear cache** button.
* **Batch translation.** Lines are translated ~30 at a time in one JSON call (the batch is the
  context, so neighbours are no longer sent twice). Each line carries its target length. A line
  missing from the answer is asked again on its own, so a bad answer never breaks a job.
* **Predict before paying for a voice.** The spoken length of each line is predicted from its
  length in characters (per-language rate in `languages.py`, calibrated during the job on the
  real voice takes). A line that clearly cannot fit is rewritten **before** its first voice take
  (rewrites batched too), instead of paying for a take that would be thrown away.
* **Economy mode (stretch first).** A too-long line is rewritten (and voiced again) only if it
  would need more than **1.15x** speed-up; a too-short line only if it would need a slow-down
  below **0.90** to reach its minimum length. Otherwise it is just time-stretched (always
  within 0.82x–1.35x). Turn it off to get the original "rewrite first" behaviour.
* **No wasted calls.** Empty or non-speech lines (`[Music]`, `♪`, `...`) are not translated or
  voiced; identical lines in a job are voiced once; voice instructions are kept short.
* **Estimate + cap.** Once the video is analysed, the app shows an estimated cost (before cache
  savings). **Max spend per video ($)** (empty = no limit) stops a job cleanly, with a clear
  message and no broken output, if it goes above.
* **See the savings.** The header shows, for the current job, the API calls made, the results
  reused from the cache and the money saved; so do the result card and the timing JSON
  (`api_usage`, `summary.api_calls`, `summary.cache_hits`, `summary.saved_usd`).
* Optional: a cheaper **Rewrite model** (e.g. `gpt-4.1-nano`) for the length rewrites only.
  Empty = the script model (default).

On the offline test plan (8 lines) a dub went from 33 API calls (before) to 19, and 0 on a
re-run (`tests\test_economy.py` prints the table).

## Output
Each job gets a folder in `output\` (or the folder chosen in Settings):

| File | Content |
|---|---|
| `Title [FR].mp4` | original video + dubbed audio (H.264/AAC, fast-start) |
| `Title [FR].srt` | target-language subtitles, same timing as the voice |
| `Title [FR] - timing.json` | per-segment timing report (slots, durations, stretch factor, mode, warnings) and the estimated cost |

Logs of every run are in `output\logs\`.

## How the timing works
For every speech segment, independently:

* **Slot.** A segment may speak from its start until the next segment starts, minus
  `SAFETY_GAP` (0.04 s). The last segment may use the time until the end of the video.
* **Natural length.** This is how long the original speaker talked. It is the target for
  "too short", so real pauses after a sentence stay pauses.
* **Too long** (TTS longer than the slot): concise rewrite, new TTS, a second concise
  rewrite if needed, then speed-up up to **1.35x**.
* **Too short** (under 85% of the natural length): fuller rewrite, new TTS, a second
  expansion if needed, then slow-down down to **0.82x**. Any small remaining gap is left
  as silence (`padded`).
* **Economy mode** (default): the rewrites above are only made when stretching alone would
  need more than `ECONOMY_SPEEDUP_LIMIT` (1.15x) or less than `ECONOMY_SLOWDOWN_LIMIT` (0.90,
  relative to the minimum length); otherwise the clip is just stretched. Lines predicted not
  to fit at all are rewritten before their first voice take (this counts as the first
  rewrite attempt).
* **Placement.** Each clip is placed at its original start time. Nothing can overlap the
  next segment, and there is no cumulative drift. The video itself is never re-timed.
* **Last resort.** If a line still does not fit at 1.35x after both rewrites, its last
  fraction of a second is faded out rather than overlapping the next line. This is
  logged as a warning in the JSON report and in the app.

`timing_mode` in the report is the last adjustment applied
(`normal`, `concise_rewrite`, `speed_up`, `expanded_rewrite`, `slow_down`, `padded`).
`steps` lists all of them.

## Settings
In the app (**Settings**): the three keys + default key, the model for each job, the voice for
each language (alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer, verse),
`MAX_SPEED_UP`, `MAX_SLOW_DOWN`, `SAFETY_GAP`, `REWRITE_TRIES`, `ORIGINAL_AUDIO_VOLUME`, the
output folder and the price table, and under **Saving money**: economy mode and its two limits,
max spend per video, the cache switch, its size limit and **Clear cache**, plus an optional
rewrite model. They are applied at the start of each job.

`dubbing\config.py` holds the defaults and every other value (`SHORT_THRESHOLD`, parallel
workers, max video height...). Some can also be set as environment variables:
`DUBBING_TEXT_MODEL`, `DUBBING_TTS_MODEL`, `DUBBING_TRANSCRIBE_MODEL`,
`DUBBING_OUTPUT_DIR`, `DUBBING_MAX_WORKERS`, `DUBBING_MAX_HEIGHT`,
`DUBBING_ORIGINAL_VOLUME`, `DUBBING_REWRITE_MODEL`, `DUBBING_CACHE=0` (no persistent cache),
`DUBBING_KEEP_TEMP=1` (keeps intermediate files for debugging).

**Adding a language.** Add one entry in `dubbing\languages.py`
(code, voice, speaking rate, voice style). It then appears in the dropdown automatically.

## Project structure
```
app.py                  starts the local server and opens the app window
cli.py                  command line: python cli.py <url> French (uses the same settings)
static\                 the interface: index.html, app.css, app.js (no build step)
dubbing\
  config.py             default settings + price table
  settings.py           user settings in %APPDATA% (keys, models, voices, timing)
  costs.py              spend tracking (cost estimate per call, ledger)
  web.py                Flask routes (JSON API + live events)
  jobs.py               background job, live state, review pause, cancel, temp files
  languages.py          language / voice table
  pipeline.py           the full workflow (+ optional hooks / cancel for the interface)
  downloader.py         URL validation + yt-dlp download
  ffmpeg_utils.py       probe, extract, time-stretch, mux (FFmpeg)
  engine.py             OpenAI: transcription, translation, rewrites, TTS (3 clients, batch calls)
  economy.py            cache / de-duplication / budget wrapper around any engine, length prediction
  cache.py              persistent content-addressed cache (%APPDATA%\VideoDubbingV3\cache)
  script.py             batch translation and pre-voice rewrites (with recovery)
  fake_engine.py        offline stand-in for OpenAI + YouTube (tests, demo mode)
  segments.py           segment model, clean-up, timing slots
  timing.py             bidirectional adaptive timing
  outputs.py            audio assembly, SRT, timing JSON
  validate.py           final quality checks
  retry.py              retries for API calls
tests\test_offline.py   offline end-to-end dub test (no API key needed)
tests\test_app.py       spend, settings and web backend tests (no API key needed)
tests\test_economy.py   cache, batching, economy mode, budget cap, de-duplication (no API key needed)
```

## Tests (no API key, no internet)
```
.venv\Scripts\python.exe -m tests.test_offline
.venv\Scripts\python.exe -m tests.test_app
.venv\Scripts\python.exe -m tests.test_economy
```
To try the interface with no key and no cost, set `DUBBING_FAKE_ENGINE=1` before `run.bat`:
the app then uses a fake AI and a generated test video instead of YouTube
(`DUBBING_FAKE_DELAY=0.3` slows it down so you can watch each step).

## Costs and notes
* Uses your OpenAI account: Whisper transcription, a GPT model for translation and
  rewrites, and gpt-4o-mini-tts for the voice. Longer videos cost more.
* Processing takes several minutes for a 10-minute video. Segments are processed 4 at a time.
* You are responsible for making sure you may use and republish the source video.
