/** Streaming, band-limited 16 kHz resampling. Sample indices, never timers, are the clock. */
export class Resampler {
  private buffer: number[] = [];
  private base = 0;
  private inputCount = 0;
  private outputCount = 0;
  private readonly radius = 24;
  private readonly phases: Float64Array[] = [];
  constructor(readonly inputRate: number, readonly outputRate = 16000) {
    if (!Number.isFinite(inputRate) || inputRate < 16000 || inputRate > 192000) throw new Error('Unsupported microphone sample rate');
    const cutoff = Math.min(1, outputRate / inputRate) * 0.94;
    for (let p = 0; p < 1024; p++) {
      const weights = new Float64Array(this.radius * 2);
      let total = 0;
      for (let j = 0; j < weights.length; j++) {
        const distance = j - this.radius + 1 - p / 1024;
        const x = Math.PI * cutoff * distance;
        const sinc = Math.abs(x) < 1e-10 ? 1 : Math.sin(x) / x;
        const window = Math.abs(distance) > this.radius ? 0 : 0.42 + 0.5 * Math.cos(Math.PI * distance / this.radius) + 0.08 * Math.cos(2 * Math.PI * distance / this.radius);
        weights[j] = cutoff * sinc * window;
        total += weights[j]!;
      }
      for (let j = 0; j < weights.length; j++) weights[j] = weights[j]! / total;
      this.phases.push(weights);
    }
  }
  push(input: Float32Array, final = false): Float32Array {
    for (const value of input) this.buffer.push(Number.isFinite(value) ? value : 0);
    this.inputCount += input.length;
    const output: number[] = [];
    const target = Math.floor(this.inputCount * this.outputRate / this.inputRate);
    while (this.outputCount < target) {
      const position = this.outputCount * this.inputRate / this.outputRate;
      const center = Math.floor(position);
      if (!final && center + this.radius >= this.inputCount) break;
      const phase = Math.min(1023, Math.floor((position - center) * 1024));
      const weights = this.phases[phase]!;
      let value = 0;
      for (let j = 0; j < weights.length; j++) value += (this.buffer[center + j - this.radius + 1 - this.base] ?? 0) * weights[j]!;
      output.push(Math.max(-1, Math.min(1, value)));
      this.outputCount++;
    }
    const discard = Math.max(0, Math.floor(this.outputCount * this.inputRate / this.outputRate) - this.radius - this.base);
    if (discard > 0) { this.buffer.splice(0, discard); this.base += discard; }
    return new Float32Array(output);
  }
}

export function encodeWav(samples: Float32Array): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const ascii = (offset: number, text: string) => { for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i)); };
  ascii(0, 'RIFF'); view.setUint32(4, buffer.byteLength - 8, true); ascii(8, 'WAVE'); ascii(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  ascii(36, 'data'); view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) { const sample = Math.max(-1, Math.min(1, samples[i]!)); view.setInt16(44 + i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true); }
  return buffer;
}
