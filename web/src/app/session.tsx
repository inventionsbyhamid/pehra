import { createContext, useContext } from "react";
import type { Me, Purposes, Role } from "../types";

const RANK: Record<Role, number> = { viewer: 0, supervisor: 1, admin: 2, owner: 3 };

export interface Session {
  me: Me;
  purposes: Purposes | null;
  reload: () => void;
  signOut: () => void;
}

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside the signed in app");
  return session;
}

export function atLeast(role: Role | null | undefined, need: Role): boolean {
  return RANK[role ?? "viewer"] >= RANK[need];
}

export function useCan(need: Role): boolean {
  return atLeast(useSession().me.role, need);
}
