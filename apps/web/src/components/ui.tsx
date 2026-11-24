import clsx from "clsx";
import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from "react";

type Tone = "neutral" | "good" | "warn" | "bad" | "info" | "accent";

const TONES: Record<Tone, string> = {
  neutral: "bg-stone-100 text-stone-700 ring-stone-200",
  good: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  warn: "bg-amber-50 text-amber-900 ring-amber-200",
  bad: "bg-rose-50 text-rose-800 ring-rose-200",
  info: "bg-sky-50 text-sky-800 ring-sky-200",
  accent: "bg-indigo-50 text-indigo-800 ring-indigo-200",
};

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
        TONES[tone],
      )}
    >
      {children}
    </span>
  );
}

export function Card({
  title,
  action,
  children,
  className,
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={clsx("rounded-xl border border-stone-200 bg-white shadow-sm", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-stone-100 px-5 py-3">
          <h2 className="text-sm font-semibold tracking-wide text-stone-900">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  busy?: boolean;
};

export function Button({ variant = "primary", busy, disabled, className, children, ...rest }: ButtonProps) {
  const styles = {
    primary: "bg-stone-900 text-white hover:bg-stone-700 focus-visible:outline-stone-900",
    secondary: "bg-white text-stone-900 ring-1 ring-inset ring-stone-300 hover:bg-stone-50 focus-visible:outline-stone-500",
    danger: "bg-white text-rose-700 ring-1 ring-inset ring-rose-300 hover:bg-rose-50 focus-visible:outline-rose-600",
    ghost: "text-stone-700 hover:bg-stone-100 focus-visible:outline-stone-500",
  }[variant];
  return (
    <button
      {...rest}
      disabled={disabled || busy}
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition-colors",
        "focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
        styles,
        className,
      )}
    >
      {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

const inputCls =
  "w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm text-stone-900 placeholder:text-stone-400 " +
  "focus:border-stone-900 focus:outline-none focus:ring-1 focus:ring-stone-900 disabled:bg-stone-50";

export function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm font-medium text-stone-800">
        {label}
        {required && <span className="text-rose-600"> *</span>}
      </span>
      {children}
      {hint && <span className="block text-xs text-stone-500">{hint}</span>}
    </label>
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={clsx(inputCls, props.className)} />;
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={clsx(inputCls, "leading-relaxed", props.className)} />;
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 py-6 text-sm text-stone-500">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {label ?? "Loading"}
    </div>
  );
}

export function Notice({ tone = "bad", children }: { tone?: "bad" | "warn" | "info" | "good"; children: ReactNode }) {
  return (
    <div
      role={tone === "bad" ? "alert" : "status"}
      className={clsx("rounded-lg px-4 py-3 text-sm ring-1 ring-inset", TONES[tone])}
    >
      {children}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-stone-300 px-6 py-10 text-center">
      <p className="font-medium text-stone-800">{title}</p>
      {children && <div className="mt-1 text-sm text-stone-500">{children}</div>}
    </div>
  );
}

export function tierTone(t: string): Tone {
  return t === "A" ? "good" : t === "B" ? "info" : "neutral";
}

export function fmtDate(iso?: string | null) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
