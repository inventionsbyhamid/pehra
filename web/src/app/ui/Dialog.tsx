import { useRef, type ReactNode } from "react";

export function useDialog() {
  const ref = useRef<HTMLDialogElement>(null);
  return { ref, open: () => ref.current?.showModal(), close: () => ref.current?.close() };
}

export function Dialog({ dialog, title, id, children, onClose }: {
  dialog: ReturnType<typeof useDialog>;
  title: string;
  id: string;
  children: ReactNode;
  onClose?: () => void;
}) {
  return (
    <dialog ref={dialog.ref} className="sheet" aria-labelledby={id} onClose={onClose}>
      <div className="stack" style={{ gap: 16 }}>
        <h2 id={id} className="h2">{title}</h2>
        {children}
      </div>
    </dialog>
  );
}
