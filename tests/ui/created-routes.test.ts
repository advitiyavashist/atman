import { describe, expect, it } from "vitest";
import {
  CREATED_ROUTES,
} from "./support/render-live";
import {
  assertOpenapiSpecReadable,
  createdRouteSuffix,
  expectedCreatedRouteSuffixes,
  loadPost201PathTemplates,
} from "./support/openapi-post-201";

describe("render-live CREATED_ROUTES stays aligned with openapi.yaml POST+201 routes", () => {
  it("reads the frozen contract spec", () => {
    assertOpenapiSpecReadable();
  });

  it("covers every contract POST+201 path", () => {
    const post201 = loadPost201PathTemplates();
    for (const template of post201) {
      const suffix = createdRouteSuffix(template);
      expect(CREATED_ROUTES.some((entry) => template.endsWith(entry) || suffix === entry)).toBe(true);
    }
  });

  it("lists only suffixes that correspond to a contract POST+201 route", () => {
    const expected = new Set(expectedCreatedRouteSuffixes());
    for (const suffix of CREATED_ROUTES) {
      expect(expected.has(suffix)).toBe(true);
    }
  });

  it("includes invitations routes T-300 found missing", () => {
    const post201 = new Set(loadPost201PathTemplates());
    expect(post201.has("/invitations")).toBe(true);
    expect(post201.has("/invitations/exchange")).toBe(true);
    expect(CREATED_ROUTES).toContain("/invitations");
    expect(CREATED_ROUTES).toContain("/invitations/exchange");
  });
});
