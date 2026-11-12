# Community-1 automatic Kaggle worker

This is inference, not training. The original evaluated Community-1 revision and
Whisper small.en are used. No speaker accuracy target is guaranteed.

1. Start the app with run.ps1 (http://127.0.0.1:8009, the only port: the Kaggle worker API is served under /gpu). Open Setup → Connect Kaggle worker.
2. Download the session notebook and import it into a PRIVATE Kaggle notebook.
3. Enable GPU and Internet. Add HF_TOKEN to Kaggle Secrets and grant notebook access.
   The Hugging Face account must have accepted Community-1 access conditions.
4. Run setup; if libraries were installed, restart the session and run only the
   worker cell. Leave that cell running. Setup in the demo will show Worker online.
5. For each recording choose Community-1 · Kaggle worker and Create transcript.
   Audio transfers automatically and the result is applied by the server without
   requiring that recording's browser tab to remain open. Prior transcripts are
   backed up and stale results are rejected. Optional known speaker count is passed
   through, but is not proof of correct attribution.

Keep the local server and Kaggle worker running. Kaggle is not an always-on hosted
API: sessions and GPU quota can expire. The temporary bridge expires after eight
hours. Connect again and import the new session notebook after expiration or a
server restart. Do not share notebooks containing the connection key. Audio travels
through Cloudflare to Kaggle; only queued jobs are exposed by the authenticated
bridge, not the main app or its recording library.

Cancellation closes the job and preserves the old transcript. GPU inference may
finish its current operation before the worker notices the closed job. No automatic
fallback to legacy CPU occurs. If the worker disconnects or fails, reconnect and
retry from the recording. Results in /kaggle/working are temporary unless saved.

Validation: local simulated worker/API tests cover authenticated queue and automatic
apply, stale result rejection, cancellation, offline rejection, and worker failure;
existing GPU exchange/connection tests pass. Real Kaggle inference is still required
to verify the runtime/network end to end.
