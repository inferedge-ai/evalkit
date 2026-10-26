import { describe, expect, it } from "vitest";
import {
  canManageMembers,
  canUseAgent,
  canWrite,
  groupMembers,
  isReviewerOf,
} from "./access";
import type { Me, MemberRow } from "./types";

const akshat: Me = {
  username: "akshat",
  is_admin: false,
  personal_workspace: "akshat",
  roles: { dev: ["writer"], prod: ["reviewer"] },
};
const admin: Me = {
  ...akshat,
  username: "admin",
  is_admin: true,
  personal_workspace: "admin",
  roles: {},
};

describe("access", () => {
  it("writes own personal workspace and shared ones as writer or admin", () => {
    expect(canWrite(akshat, { name: "akshat", type: "personal" })).toBe(true);
    expect(canWrite(admin, { name: "akshat", type: "personal" })).toBe(false);
    expect(canWrite(akshat, { name: "dev", type: "shared" })).toBe(true);
    expect(canWrite(akshat, { name: "prod", type: "shared" })).toBe(false);
    expect(canWrite(admin, { name: "prod", type: "shared" })).toBe(true);
  });

  it("reviews only where the role is held", () => {
    expect(isReviewerOf(akshat, "prod")).toBe(true);
    expect(isReviewerOf(akshat, "dev")).toBe(false);
    expect(isReviewerOf(admin, "prod")).toBe(false);
  });

  it("only admins manage members, of shared workspaces", () => {
    expect(canManageMembers(admin, { type: "shared" })).toBe(true);
    expect(canManageMembers(admin, { type: "personal" })).toBe(false);
    expect(canManageMembers(akshat, { type: "shared" })).toBe(false);
  });

  it("uses shared agents and its own personal ones", () => {
    expect(
      canUseAgent(akshat, { workspace: "dev", workspace_type: "shared" }),
    ).toBe(true);
    expect(
      canUseAgent(akshat, { workspace: "akshat", workspace_type: "personal" }),
    ).toBe(true);
    expect(
      canUseAgent(akshat, { workspace: "bob", workspace_type: "personal" }),
    ).toBe(false);
  });
});

describe("groupMembers", () => {
  const grant = (username: string, role: MemberRow["role"]): MemberRow => ({
    username,
    role,
    granted_by: "admin",
    created_at: "2026-10-14T08:00:00Z",
  });

  it("puts every grant of a user on one row, writer first", () => {
    const members = groupMembers([
      grant("ravi", "reviewer"),
      grant("akshat", "reviewer"),
      grant("akshat", "writer"),
    ]);
    expect(members.map((m) => m.username)).toEqual(["akshat", "ravi"]);
    expect(members[0].grants.map((g) => g.role)).toEqual([
      "writer",
      "reviewer",
    ]);
  });
});
