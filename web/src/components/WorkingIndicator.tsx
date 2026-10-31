import { useEffect, useState } from "react";
import { AgentAvatar } from "./AgentAvatar";

const CHAT_STEPS = [
  "Understanding your request…",
  "Routing to the right specialist…",
  "Inspecting your AWS resources…",
  "Reasoning about what I found…",
  "Drafting a careful answer…",
  "Still working — big requests take a while…",
];
const APPROVE_STEPS = ["Running the approved change…", "Talking to AWS…", "Waiting for resources to be ready…", "Verifying the result…"];

export function WorkingIndicator({ kind = "chat", seconds }: { kind?: "chat" | "approve"; seconds: number }) {
  const steps = kind === "chat" ? CHAT_STEPS : APPROVE_STEPS;
  const [i, setI] = useState(0);
  useEffect(() => { setI(Math.min(Math.floor(seconds / 7), steps.length - 1)); }, [seconds, steps.length]);
  return (
    <div className="flex animate-rise items-start gap-3">
      <AgentAvatar mood="thinking" size={36} />
      <div className="panel px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <span className="flex gap-1">
            {[0, 1, 2].map((d) => (
              <span key={d} className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-400" style={{ animationDelay: `${d * 120}ms` }} />
            ))}
          </span>
          {steps[i]}
        </div>
        <div className="mt-1 text-xs muted">{seconds}s</div>
      </div>
    </div>
  );
}
