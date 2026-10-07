# Demo integration — transcript and draft summary

Run `powershell -ExecutionPolicy Bypass -File .\run.ps1 -Port 8009`.

- Upload audio and use the existing local speech pipeline or configured Deepgram API.
- To use the evaluated Whisper small.en + Community-1 outputs, download `transcript_with_speakers.json` from Colab, then select **Import Colab transcript**. This creates a separate recording, preserving the original. Imported audio is not attached. This does not switch local inference to Community-1.
- Use **Edit** on a turn to correct words or choose an existing speaker. Originals and timestamps remain saved; word timings refer to the original ASR. A correction invalidates the summary. Adjacent turns remain separate to preserve source IDs.
- Select **Summarize meeting** for discussion highlights. Each point has expandable original evidence. AI drafts are unverified; valid IDs do not prove semantic correctness. Assignments and deadlines are intentionally excluded.
- Export transcript and summary separately. Both persist across restarts.

## Local summary on Windows
Run `powershell -ExecutionPolicy Bypass -File .\setup-summary.ps1 -Install` once. This downloads portable Ollama and qwen2.5:3b to this project's data folder, not C. Requires network and several GB of disk space. The model is quantized and is not identical to Colab FP16 inference.
Set in `.env`:
```
OLLAMA_URL=http://127.0.0.1:11435
OLLAMA_MODEL=qwen2.5:3b
```
Restart the demo. On subsequent computer restarts run `setup-summary.ps1` to start the local model service. It runs in the background on loopback port 11435. CPU inference can take minutes. Logs are in `data/ollama-runtime`.

## Optional API
Configure LLM_API_URL (base URL including /v1 when required), LLM_API_KEY, LLM_API_MODEL in .env and restart. Choose API explicitly in the summary selector. Transcript text is sent to that provider and charges may apply. Keys are never returned to the browser.

## Checks
Run `.venv\Scripts\python.exe -m unittest discover -s tests -v`.
API integration tests use a fake language-model response; they do not establish model quality.
