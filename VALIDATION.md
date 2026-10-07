# Upload transcription validation — 2026-09-23

Verified on this Windows machine using CPU, faster-whisper 1.2.1 / base.en INT8 and sherpa-onnx 1.13.8. Backend: `app.step_one:app`.

| Check | Result |
|---|---|
| Automated API / transformation tests | 14 passed |
| Frontend JavaScript syntax | `node --check` passed |
| Public English sample 1, 16 seconds, Auto | Ready in 7.8 seconds; 2 speaker groups |
| Public English sample 2, 34 seconds, Auto | Ready in 17.1 seconds; 3 groups for a sample labelled two speakers — over-segmentation |
| Same sample 2, known count = 2 | Ready in 17.3 seconds; 2 groups |
| Three seconds of generated silence through actual model | Empty transcript |
| Cancellation during actual local diarization | ProcessingCancelled returned |
| Audio playback HTTP range / TXT export / persistence | Passed |

Sample source: [sherpa-onnx speaker segmentation release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models), files `1-two-speakers-en.wav` and `2-two-speakers-en.wav`. Raw outputs and source URLs are saved locally in `data/evaluation/report.json`; the known-count rerun is in `data/evaluation/known-two-speakers.json`.

These checks confirm operation, not word or speaker accuracy. No reference-aligned WER/DER measurement was performed. A known speaker count constrains clustering but does not prove every turn has the correct label. Auto clustering remains sensitive to short speech; threshold trials of 0.6 and 0.7 did not resolve sample 2, so the original 0.5 default is retained. Unassigned words remain visible. Point timestamps are attributed only when one speaker is active.

Deepgram was not exercised because no key is configured. Browser automation was unavailable due to a computer-use tool initialization error; UI changes were checked at source and HTTP level, not by an automated browser interaction test.
