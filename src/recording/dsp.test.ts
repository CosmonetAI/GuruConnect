import { describe, it, expect } from 'vitest';
import { encodeWav, Resampler } from './dsp';
function resample(rate: number, seconds: number, frequency: number, blocks = 128) {
  const input = new Float32Array(Math.round(rate * seconds));
  for (let i=0; i<input.length; i++) input[i] = 0.5 * Math.sin(2*Math.PI*frequency*i/rate);
  const resampler = new Resampler(rate); const batches: Float32Array[] = [];
  for (let i=0;i<input.length;i+=blocks) batches.push(resampler.push(input.subarray(i,i+blocks)));
  batches.push(resampler.push(new Float32Array(),true));
  const output = new Float32Array(batches.reduce((sum,row) => sum+row.length,0));let at=0;
  for (const row of batches) {output.set(row,at);at+=row.length;} return output;
}
describe('real PCM capture DSP', () => {
  for (const rate of [16000,44100,48000,96000]) {
    it(`keeps exact sample clock and in-band amplitude at ${rate} Hz`,()=>{
      const output=resample(rate,1.125,1000);
      expect(output.length).toBe(18000);
      const rms=Math.sqrt(output.slice(100,-100).reduce((sum,x)=>sum+x*x,0)/(output.length-200));
      expect(rms).toBeCloseTo(0.5/Math.sqrt(2),2);
    });
  }
  it('filters 12 kHz energy instead of aliasing to 4 kHz',()=>{
    const output=resample(48000,1,12000);
    const rms=Math.sqrt(output.slice(100,-100).reduce((sum,x)=>sum+x*x,0)/(output.length-200));
    expect(rms).toBeLessThan(0.005);
  });
  it('streaming boundaries produce the same samples as one input block',()=>{
    const streamed=resample(44100,0.133,1700,128); const whole=resample(44100,0.133,1700,999999);
    expect(streamed).toEqual(whole);
  });
  it('encodes a final short chunk as an independent little-endian WAV',()=>{
    const buffer=encodeWav(new Float32Array([-1,0,1])); const view=new DataView(buffer);
    expect(buffer.byteLength).toBe(50); expect(view.getUint32(4,true)).toBe(42);
    expect(view.getUint16(22,true)).toBe(1);expect(view.getUint32(24,true)).toBe(16000);
    expect(view.getUint32(40,true)).toBe(6);expect(view.getInt16(44,true)).toBe(-32768);expect(view.getInt16(48,true)).toBe(32767);
  });
  it('rejects invalid rates and sanitizes non-finite microphone samples',()=>{
    expect(()=>new Resampler(0)).toThrow();const r=new Resampler(16000);
    const output=r.push(new Float32Array([NaN,Infinity,-Infinity]),true);
    expect([...output].every(Number.isFinite)).toBe(true);
  });
});
