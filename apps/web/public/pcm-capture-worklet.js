// AudioWorklet: forwards raw microphone samples to the main thread in ~43 ms frames.
// Raw PCM (not MediaRecorder) so the app can keep a pre-roll buffer and cut exact utterances.
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frame = new Float32Array(2048);
    this.filled = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      for (let i = 0; i < channel.length; i++) {
        this.frame[this.filled++] = channel[i];
        if (this.filled === this.frame.length) {
          this.port.postMessage(this.frame.slice());
          this.filled = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
