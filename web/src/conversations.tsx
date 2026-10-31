import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { uid, useLocalStorage } from "./lib";
import type { Artifact, Message } from "./types";

export interface Conversation {
  id: string;
  title: string;
  updatedAt: number;
  messages: Message[];
  artifact: Artifact | null;
}

interface Store {
  conversations: Conversation[];
  active: Conversation;
  select: (id: string) => void;
  create: () => void;
  remove: (id: string) => void;
  update: (id: string, change: (c: Conversation) => Conversation) => void;
}

const Ctx = createContext<Store | null>(null);
const MAX_CONVERSATIONS = 25;
const blank = (): Conversation => ({ id: uid(), title: "New conversation", updatedAt: Date.now(), messages: [], artifact: null });

/** Conversations live in this browser (per user); the agent itself is stateless between turns. */
export function ConversationProvider({ userKey, children }: { userKey: string; children: ReactNode }) {
  const [list, setList] = useLocalStorage<Conversation[]>(`cloudops-conversations-${userKey}`, [blank()]);
  const [activeId, setActiveId] = useLocalStorage<string>(`cloudops-active-${userKey}`, list[0]?.id ?? "");

  const conversations = list.length ? list : [blank()];
  const active = conversations.find((c) => c.id === activeId) ?? conversations[0];

  const update = useCallback((id: string, change: (c: Conversation) => Conversation) => {
    setList((prev) => prev.map((c) => (c.id === id ? { ...change(c), updatedAt: Date.now() } : c)));
  }, [setList]);

  const create = useCallback(() => {
    const fresh = blank();
    setList((prev) => [fresh, ...prev.filter((c) => c.messages.length)].slice(0, MAX_CONVERSATIONS));
    setActiveId(fresh.id);
  }, [setList, setActiveId]);

  const remove = useCallback((id: string) => {
    setList((prev) => {
      const next = prev.filter((c) => c.id !== id);
      return next.length ? next : [blank()];
    });
  }, [setList]);

  const value = useMemo<Store>(() => ({
    conversations: [...conversations].sort((a, b) => b.updatedAt - a.updatedAt),
    active, select: setActiveId, create, remove, update,
  }), [conversations, active, setActiveId, create, remove, update]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useConversations() {
  const store = useContext(Ctx);
  if (!store) throw new Error("useConversations outside ConversationProvider");
  return store;
}
