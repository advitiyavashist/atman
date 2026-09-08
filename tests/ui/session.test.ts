import { afterEach, describe, expect, it, vi } from "vitest";
import { resolveSession } from "../../ui/src/session";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
  document.cookie = "tb_session=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

describe("how the dashboard finds its board session", () => {
  it("reads the host's probe and never sees the session token", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ project_id: "prj_demo0001", csrf_token: "csrf-1", base_url: "/api" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const result = await resolveSession(fetchMock as unknown as typeof fetch);
    expect(result.status).toBe("ready");
    if (result.status !== "ready") return;
    expect(result.session.source).toBe("hosted");
    expect(result.session.config.projectId).toBe("prj_demo0001");
    expect(result.session.config.csrfToken).toBe("csrf-1");
    // The whole point of the host proxy: the credential stays in that process.
    expect(JSON.stringify(result.session.config)).not.toMatch(/session_token|tb_session/);
  });

  it("distinguishes 'host up, no board' from 'no host at all'", async () => {
    // A host that is running but has no board session answers 200 with no
    // project_id. Collapsing that into a 404 would tell the operator to start
    // the dashboard when what they need to start is the board.
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ session_file: "/tmp/x/operator-session.json" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const result = await resolveSession(fetchMock as unknown as typeof fetch);
    expect(result.status).toBe("unconfigured");
    if (result.status !== "unconfigured") return;
    expect(result.reason).toMatch(/Start the board server/);
  });

  it("takes a one-time ?token= and strips it from the address bar", async () => {
    window.history.replaceState({}, "", "/?token=secret-token&project=prj_demo0001&csrf=csrf-2");
    const result = await resolveSession(vi.fn() as unknown as typeof fetch);

    expect(result.status).toBe("ready");
    if (result.status !== "ready") return;
    expect(result.session.source).toBe("url-token");
    expect(result.session.config.projectId).toBe("prj_demo0001");
    expect(document.cookie).toMatch(/tb_session=secret-token/);
    // A token left in the URL survives in history, in a screenshot, and in the
    // next page's Referer.
    expect(window.location.search).not.toMatch(/token|project|csrf/);
  });

  it("reports no session rather than inventing one when nothing is configured", async () => {
    const fetchMock = vi.fn(async () => new Response("nope", { status: 404 }));
    const result = await resolveSession(fetchMock as unknown as typeof fetch);
    expect(result.status).toBe("unconfigured");
  });

  it("reports no session when the probe itself cannot be reached", async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    const result = await resolveSession(fetchMock as unknown as typeof fetch);
    expect(result.status).toBe("unconfigured");
  });
});
