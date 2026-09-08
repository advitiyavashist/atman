import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { Activity } from "../../ui/src/screens/Activity";
import { boardFetch, renderLive } from "./support/render-live";
import activityPopulated from "../../ui/src/fixtures/data/activity/populated.json";

describe("Activity, reading a live board", () => {
  it("shows each event's occurred_at as local wall time, not a raw UTC Z string", async () => {
    renderLive(<Activity />, boardFetch({ "GET /activity": activityPopulated }));
    const first = activityPopulated.items[0];
    await waitFor(() => expect(screen.getByTestId(`activity-time-${first.id}`)).toBeInTheDocument());
    const stamp = screen.getByTestId(`activity-time-${first.id}`);
    expect(stamp).toHaveAttribute("dateTime", first.occurred_at);
    expect(stamp.textContent).not.toMatch(/T\d{2}:\d{2}:\d{2}Z/);
    expect(stamp.textContent).toMatch(/ago|just now|in /);
  });
});
