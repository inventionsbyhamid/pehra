// backend/watchful/data/readers.json, aliased in vite.config.ts: what a camera can have turned on in its settings
declare module "@data/readers" {
  // purposes: the camera purposes a reader may be turned on for, when it is limited (staff recognition: entry cameras)
  const readers: Record<string, { label: string; about: string; attributes: string[]; purposes?: string[]; refused?: string }>;
  export default readers;
}
