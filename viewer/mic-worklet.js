const TARGET_RATE = 16000;
const FRAME_SAMPLES = 960;
const MIC_GAIN = 4;

class MicCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this._pending = [];
    this._ratio = sampleRate / TARGET_RATE;
    this._read = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel || !channel.length) return true;
    this._pending.push(Float32Array.from(channel));
    let available = 0;
    for (let i = 0; i < this._pending.length; i++) available += this._pending[i].length;
    const needed = Math.ceil((FRAME_SAMPLES + 1) * this._ratio);
    if (available - this._read < needed) return true;

    const pcm = new Int16Array(FRAME_SAMPLES);
    for (let i = 0; i < FRAME_SAMPLES; i++) {
      const pos = this._read + i * this._ratio;
      const index = Math.floor(pos);
      const frac = pos - index;
      const a = this._at(index);
      const b = this._at(index + 1);
      const sample = (a + (b - a) * frac) * MIC_GAIN;
      // Soft-clip instead of hard-clipping: tanh keeps loud speech smooth
      // instead of flattening the waveform into harsh distortion.
      const clipped = Math.tanh(sample);
      pcm[i] = clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff;
    }
    this._read += FRAME_SAMPLES * this._ratio;
    this._trim();
    this.port.postMessage(pcm.buffer, [pcm.buffer]);
    return true;
  }

  _at(index) {
    let offset = 0;
    for (let i = 0; i < this._pending.length; i++) {
      const block = this._pending[i];
      if (index < offset + block.length) return block[index - offset];
      offset += block.length;
    }
    return 0;
  }

  _trim() {
    while (this._pending.length && this._read >= this._pending[0].length) {
      this._read -= this._pending[0].length;
      this._pending.shift();
    }
  }
}

registerProcessor("mic-capture", MicCapture);
