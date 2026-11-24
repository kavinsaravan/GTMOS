import clsx from "clsx";
import { Check } from "lucide-react";

export const STEPS = ["Intake", "ICP", "Discover", "Review", "Track"] as const;

/** `current` is the index of the active step; earlier steps render as done. */
export function Rail({ current }: { current: number }) {
  return (
    <ol className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm" aria-label="Progress">
      {STEPS.map((s, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <li key={s} className="flex items-center gap-2" aria-current={active ? "step" : undefined}>
            <span
              className={clsx(
                "flex size-6 items-center justify-center rounded-full text-xs font-semibold",
                done && "bg-emerald-600 text-white",
                active && "bg-stone-900 text-white",
                !done && !active && "bg-stone-200 text-stone-600",
              )}
            >
              {done ? <Check className="size-3.5" aria-hidden /> : i + 1}
            </span>
            <span className={clsx(active ? "font-semibold text-stone-900" : "text-stone-600")}>{s}</span>
            {i < STEPS.length - 1 && <span className="mx-1 h-px w-6 bg-stone-300" aria-hidden />}
          </li>
        );
      })}
    </ol>
  );
}
