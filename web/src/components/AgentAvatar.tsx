import { cn } from "../lib";

export type AgentMood = "idle" | "thinking" | "waiting" | "happy" | "sad";

/** The agent's face. Its expression follows what the agent is doing. */
export function AgentAvatar({ mood = "idle", size = 40 }: { mood?: AgentMood; size?: number }) {
  const ring = mood === "thinking" ? "bg-brand-500" : mood === "waiting" ? "bg-amber-400" : null;
  const mouth =
    mood === "happy" ? "M22 38 Q32 46 42 38" : mood === "sad" ? "M22 42 Q32 35 42 42" : mood === "waiting" ? "M24 40 H40" : "M23 39 Q32 43 41 39";
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      {ring && <span className={cn("absolute inset-0 rounded-2xl animate-pulse-ring", ring)} />}
      <svg viewBox="0 0 64 64" width={size} height={size} className={cn("relative", mood === "idle" && "animate-float")}
        role="img" aria-label={`Agent is ${mood}`}>
        <defs>
          <linearGradient id="agent-g" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#6366f1" />
            <stop offset="1" stopColor="#06b6d4" />
          </linearGradient>
        </defs>
        <rect x="2" y="2" width="60" height="60" rx="18" fill="url(#agent-g)" />
        <line x1="32" y1="2" x2="32" y2="-6" stroke="#a5b4fc" strokeWidth="3" />
        <rect x="11" y="15" width="42" height="34" rx="12" fill="#0b0c16" opacity="0.92" />
        <g style={{ transformOrigin: "32px 30px", animation: "blink 4s infinite" }}>
          {mood === "thinking" ? (
            <>
              <circle cx="24" cy="29" r="3.5" fill="#22d3ee"><animate attributeName="cy" values="29;26;29" dur="0.9s" repeatCount="indefinite" /></circle>
              <circle cx="40" cy="29" r="3.5" fill="#22d3ee"><animate attributeName="cy" values="26;29;26" dur="0.9s" repeatCount="indefinite" /></circle>
            </>
          ) : mood === "happy" ? (
            <>
              <path d="M20 30 Q24 25 28 30" stroke="#22d3ee" strokeWidth="3" fill="none" strokeLinecap="round" />
              <path d="M36 30 Q40 25 44 30" stroke="#22d3ee" strokeWidth="3" fill="none" strokeLinecap="round" />
            </>
          ) : (
            <>
              <circle cx="24" cy="29" r="3.5" fill={mood === "waiting" ? "#fbbf24" : "#22d3ee"} />
              <circle cx="40" cy="29" r="3.5" fill={mood === "waiting" ? "#fbbf24" : "#22d3ee"} />
            </>
          )}
        </g>
        <path d={mouth} stroke="#e0e7ff" strokeWidth="2.5" fill="none" strokeLinecap="round" />
      </svg>
    </div>
  );
}
