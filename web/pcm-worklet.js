class PCMRecorder extends AudioWorkletProcessor {
  constructor() { super(); this.buffer = new Int16Array(2048); this.offset = 0; }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const sample of input) {
      this.buffer[this.offset++] = Math.round(Math.max(-1, Math.min(1, sample)) * 32767);
      if (this.offset === this.buffer.length) {
        this.port.postMessage(this.buffer.buffer, [this.buffer.buffer]);
        this.buffer = new Int16Array(2048); this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor('pcm-recorder', PCMRecorder);
