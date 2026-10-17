import { CircleAlert, LoaderCircle } from "lucide-react";

export const LoadingState = ({ label = "正在读取本地资料…" }: { label?: string }) => (
  <p className="section-state" role="status" aria-live="polite"><LoaderCircle aria-hidden="true" size={18} />{label}</p>
);

export const ErrorState = ({ message, onRetry }: { message: string; onRetry: () => void }) => (
  <section className="section-state section-state--error" role="alert">
    <CircleAlert aria-hidden="true" size={18} />
    <p>{message}</p>
    <button type="button" className="button button--quiet" onClick={onRetry}>重新读取一次</button>
  </section>
);
