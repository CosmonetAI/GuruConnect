import { Resampler, encodeWav } from './dsp';

declare const sampleRate: number;
declare class AudioWorkletProcessor { readonly port: MessagePort; constructor(); }
declare function registerProcessor(name: string, processor: typeof AudioWorkletProcessor): void;

class CaptureProcessor extends AudioWorkletProcessor {
  private resampler = new Resampler(sampleRate);
  private pending: number[] = [];
  private paused = false;
  private ended = false;
  private sequence = 0;
  private total = 0;
  private blocks = 0;
  private maxSamples: number;
  private chunkSamples: number;
  constructor(options?: { processorOptions?: { maxSamples?: number; chunkSamples?: number } }) {
    super();
    this.maxSamples = options?.processorOptions?.maxSamples ?? 115200000;
    this.chunkSamples = Math.max(1, Number(options?.processorOptions?.chunkSamples) || 3 * 60 * 16000);
    this.port.onmessage = ({ data }: MessageEvent<{ type: string; id: number }>) => {
      if (data.type === 'pause' || data.type === 'stop') {
        this.add(this.resampler.push(new Float32Array(), true));
        this.flush(); this.paused = true;
        this.ended = data.type === 'stop';
      } else if (data.type === 'resume') { this.resampler = new Resampler(sampleRate); this.paused = false; }
      this.port.postMessage({ type: 'ack', id: data.id, samples: this.total });
    };
  }
  private add(samples: Float32Array) {
    const room = Math.max(0, this.maxSamples - this.total - this.pending.length);
    for (const value of samples.subarray(0,room)) this.pending.push(value);
    // The page chooses the chunk duration: walk-ins use 30 seconds, sessions use 3 minutes.
    while (this.pending.length >= this.chunkSamples) this.emit(this.chunkSamples);
    if (this.total + this.pending.length >= this.maxSamples && !this.paused) {
      this.flush(); this.paused = true; this.port.postMessage({ type: 'limit', samples: this.total });
    }
  }
  private emit(count: number) {
    if (!count) return;
    const buffer = encodeWav(new Float32Array(this.pending.splice(0, count)));
    this.port.postMessage({ type: 'chunk', sequence: this.sequence++, startSample: this.total, sampleCount: count, buffer }, [buffer]);
    this.total += count;
  }
  private flush() { this.emit(this.pending.length); }
  process(inputs: Float32Array[][]): boolean {
    if (this.ended) return false;
    if (this.paused) return true;
    const channels = inputs[0];
    const first = channels?.[0];
    if (!first) return true;
    const mono = new Float32Array(first.length);
    let square = 0;
    for (let i = 0; i < mono.length; i++) {
      let sum = 0;
      for (const channel of channels) sum += channel[i] ?? 0;
      mono[i] = sum / channels.length; square += mono[i]! * mono[i]!;
    }
    this.add(this.resampler.push(mono));
    if (++this.blocks % 16 === 0) this.port.postMessage({ type: 'meter', level: Math.sqrt(square / mono.length), samples: this.total + this.pending.length });
    return true;
  }
}
registerProcessor('guru-pcm-capture', CaptureProcessor);
