# Shorts Factory

Clip -> (gate 1: you approve) -> AI script -> AI voice -> render 9:16 with captions -> (gate 2: you approve) -> scheduled YouTube upload.

## Setup
    pip install -r requirements.txt        # ffmpeg must be on PATH
    export ANTHROPIC_API_KEY=...           # if llm.provider: anthropic
Edit `config.yaml`: set `voice.engine: edge-tts`, `llm.provider: anthropic`, `transcribe.engine`.

## Daily workflow
    python cli.py detect stream_recording.mp4 -n 5 --streamer Name --game Valorant --credit "https://twitch.tv/name"
    python cli.py add "https://..." --start 120 --end 150 --streamer Name --game GTA5   # or a single clip
    python cli.py run            # runs until it needs you
    python cli.py review         # lists clips/renders waiting on you
    python cli.py approve 3      # gate 1 or gate 2, whichever the job is waiting on
    python cli.py run            # continues: script -> voice -> render -> (gate 2) -> upload
    python cli.py set 3 --narration "..." --hook "..."; python cli.py redo 3   # fix the script, re-render
    python cli.py reject 4 --reason "boring"

Each job stores `source_credit` (added to the description) and `permission` (your own record).

## Upload setup (one-time)
1. Google Cloud project -> enable YouTube Data API v3 -> OAuth client (Desktop) -> save as `creds/client_secret.json`.
2. Set `upload.enabled: true`. First upload opens a browser for consent.
3. Uploads go in as private + `publishAt` (next 7:45 PM / 9:15 PM IST slot). Unverified API projects may be
   forced to private; apply for YouTube's API compliance audit early.

## Test
    python tests/test_e2e.py     # offline, synthetic stream

## Not built yet (next phases)
- Live capture (streamlink) feeding `detect` on a schedule
- Freeze-frame "pause before the climax" format, zoom punches, arrows/circles (needs vision model for coordinates)
- Analytics loop (YouTube Analytics API -> which hooks/formats retain)
- Tiny web UI for approvals (currently CLI)
- Duplicate/originality QA agent
