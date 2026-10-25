// Faces are blurred on this device before a frame leaves it. YuNet (OpenCV Model Zoo, MIT) runs in OpenCV.js on a
// small copy of each frame; every face it finds, grown a little and held a moment after it was last seen, becomes a
// coarse opaque mosaic, also drawn with OpenCV. The settings come from the server's Tuning (push.face_*).
import cvUrl from "@techstark/opencv-js/dist/opencv.js?url";

export type FaceSettings = { width: number; score: number; grow: number; hold_ms: number; blocks: number };

type Cv = any;
type Box = { x: number; y: number; w: number; h: number; seen: number };

const MODEL_URL = "/api/cam/face-model";
const MODEL_FILE = "/yunet.onnx";
const NMS = 0.3;
const TOP_K = 50;
const LOAD_TIMEOUT_MS = 90_000;

let opencv: Promise<Cv> | null = null;

function loadOpenCv(): Promise<Cv> {
  opencv ??= new Promise<Cv>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = cvUrl;
    script.async = true;
    script.onerror = () => reject(new Error("opencv"));
    script.onload = async () => {
      let cv = (window as unknown as { cv: Cv }).cv;
      if (cv instanceof Promise) cv = await cv;
      const ready = () => !!cv?.Mat && !!cv?.FaceDetectorYN;
      if (!ready()) {
        await new Promise<void>((done, fail) => {
          const started = Date.now();
          cv.onRuntimeInitialized = () => done();
          const id = window.setInterval(() => {
            if (ready()) {
              window.clearInterval(id);
              done();
            } else if (Date.now() - started > LOAD_TIMEOUT_MS) {
              window.clearInterval(id);
              fail(new Error("opencv"));
            }
          }, 100);
        }).catch(reject);
      }
      resolve(cv);
    };
    document.head.appendChild(script);
  });
  opencv.catch(() => (opencv = null));
  return opencv;
}

let model: Promise<void> | null = null;

function loadModel(cv: Cv): Promise<void> {
  model ??= fetch(MODEL_URL)
    .then((r) => {
      if (!r.ok) throw new Error("model");
      return r.arrayBuffer();
    })
    .then((buf) => {
      cv.FS_createDataFile("/", MODEL_FILE.slice(1), new Uint8Array(buf), true, false, false);
    });
  model.catch(() => (model = null));
  return model;
}

export class FaceBlur {
  private boxes: Box[] = [];
  private size = { w: 0, h: 0 };
  private detector: Cv;

  private constructor(private cv: Cv, private s: FaceSettings) {
    this.detector = new cv.FaceDetectorYN(MODEL_FILE, "", new cv.Size(s.width, s.width), s.score, NMS, TOP_K);
  }

  static async create(s: FaceSettings): Promise<FaceBlur> {
    const cv = await loadOpenCv();
    await loadModel(cv);
    return new FaceBlur(cv, s);
  }

  // Finds the faces on the canvas and covers them, in place. Returns how many areas are covered.
  apply(canvas: HTMLCanvasElement): number {
    const { cv, s } = this;
    const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
    const W = canvas.width;
    const H = canvas.height;
    const rgba = cv.matFromImageData(ctx.getImageData(0, 0, W, H));
    const small = new cv.Mat();
    const bgr = new cv.Mat();
    const faces = new cv.Mat();
    try {
      const dw = Math.min(s.width, W);
      const dh = Math.max(1, Math.round((H * dw) / W));
      cv.resize(rgba, small, new cv.Size(dw, dh), 0, 0, cv.INTER_AREA);
      cv.cvtColor(small, bgr, cv.COLOR_RGBA2BGR);
      if (this.size.w !== dw || this.size.h !== dh) {
        this.detector.setInputSize(new cv.Size(dw, dh));
        this.size = { w: dw, h: dh };
      }
      this.detector.detect(bgr, faces);
      const now = performance.now();
      const k = W / dw;
      for (let i = 0; i < faces.rows; i++) {
        const row = faces.data32F.subarray(i * faces.cols, i * faces.cols + 4);
        this.boxes.push({ x: row[0] * k, y: row[1] * k, w: row[2] * k, h: row[3] * k, seen: now });
      }
      this.boxes = this.boxes.filter((b) => now - b.seen <= s.hold_ms);
      for (const b of this.boxes) this.cover(rgba, b, W, H);
      ctx.putImageData(new ImageData(new Uint8ClampedArray(rgba.data), W, H), 0, 0);
      return this.boxes.length;
    } finally {
      rgba.delete();
      small.delete();
      bgr.delete();
      faces.delete();
    }
  }

  private cover(rgba: Cv, b: Box, W: number, H: number) {
    const { cv, s } = this;
    const x = Math.max(0, Math.floor(b.x - b.w * s.grow));
    const y = Math.max(0, Math.floor(b.y - b.h * s.grow));
    const w = Math.min(W, Math.ceil(b.x + b.w * (1 + s.grow))) - x;
    const h = Math.min(H, Math.ceil(b.y + b.h * (1 + s.grow))) - y;
    if (w < 2 || h < 2) return;
    const roi = rgba.roi(new cv.Rect(x, y, w, h));
    const tiny = new cv.Mat();
    try {
      const across = Math.max(1, Math.min(s.blocks, w));
      cv.resize(roi, tiny, new cv.Size(across, Math.max(1, Math.round((across * h) / w))), 0, 0, cv.INTER_AREA);
      cv.resize(tiny, roi, new cv.Size(w, h), 0, 0, cv.INTER_NEAREST);
    } finally {
      tiny.delete();
      roi.delete();
    }
  }

  close() {
    this.detector?.delete?.();
  }
}
