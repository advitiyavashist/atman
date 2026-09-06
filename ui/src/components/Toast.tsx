import { useEffect, useState } from "react";

let notify: ((message: string) => void) | null = null;

export function toast(message: string) {
  notify?.(message);
}

export function ToastHost() {
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    notify = (m: string) => setMessage(m);
    return () => {
      notify = null;
    };
  }, []);

  useEffect(() => {
    if (!message) return;
    const t = setTimeout(() => setMessage(null), 4500);
    return () => clearTimeout(t);
  }, [message]);

  if (!message) return null;
  return (
    <div id="toast" role="status">
      {message}
    </div>
  );
}
