/**
 * Hash-routing regression tests for the workspace sections: direct hash
 * deep-links must resolve to the right section (including the admin-only
 * «Пользователи» from PR #40, «Шаблоны документов» and «Лицензия»), unknown
 * hashes fall back, and navigation updates the hash.
 */

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useWorkspaceSection } from "./useWorkspaceSection";

function renderRouter(initialHash: string) {
  window.location.hash = initialHash;
  return renderHook(() => useWorkspaceSection("candidates"));
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

describe("useWorkspaceSection", () => {
  it("resolves the admin-only users deep link (PR #40 regression)", () => {
    const { result } = renderRouter("#/users");
    expect(result.current[0]).toBe("users");
  });

  it("resolves the templates and license deep links", () => {
    const templates = renderRouter("#/templates");
    expect(templates.result.current[0]).toBe("templates");
    templates.unmount();

    const license = renderRouter("#/license");
    expect(license.result.current[0]).toBe("license");
  });

  it("falls back for an unknown hash", () => {
    const { result } = renderRouter("#/does-not-exist");
    expect(result.current[0]).toBe("candidates");
  });

  it("keeps the section in sync with hashchange", async () => {
    const { result } = renderRouter("#/candidates");
    expect(result.current[0]).toBe("candidates");

    await act(async () => {
      window.location.hash = "#/users";
    });
    // jsdom диспатчит hashchange асинхронно.
    await waitFor(() => expect(result.current[0]).toBe("users"));
  });

  it("updates the hash on navigate", async () => {
    const { result } = renderRouter("#/candidates");
    const [, navigate] = result.current;

    await act(async () => {
      navigate("templates");
    });
    expect(window.location.hash).toBe("#/templates");
    expect(result.current[0]).toBe("templates");
  });
});
