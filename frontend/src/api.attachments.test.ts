import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { openSavedFile, saveCandidateAttachmentWithPicker, saveDialogAvailable } from "./api";

const fetchMock = vi.fn<typeof fetch>();
const picker = vi.fn();
const write = vi.fn();
const close = vi.fn();
const abort = vi.fn();
const handle = { name: "local-form.docx", createWritable: vi.fn(), getFile: vi.fn() };

beforeEach(() => {
  vi.resetAllMocks();
  fetchMock.mockResolvedValue(new Response("server-file", { headers: { "content-disposition": 'attachment; filename="form.docx"' } }));
  write.mockResolvedValue(undefined);
  close.mockResolvedValue(undefined);
  abort.mockResolvedValue(undefined);
  handle.createWritable.mockResolvedValue({ write, close, abort });
  picker.mockResolvedValue(handle);
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("showSaveFilePicker", picker);
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe("actual attachment file helpers (not component mocks)", () => {
  it("opens the save dialog before network I/O and waits for write/close", async () => {
    let choose!: (value: typeof handle) => void;
    picker.mockImplementation(() => new Promise((resolve) => { choose = resolve; }));
    const saving = saveCandidateAttachmentWithPicker("candidate", "attachment", "form.docx");
    expect(picker).toHaveBeenCalledWith(expect.objectContaining({ suggestedName: "form.docx" }));
    expect(fetchMock).not.toHaveBeenCalled();
    choose(handle);
    const result = await saving;
    expect(fetchMock).toHaveBeenCalledWith("/api/candidates/candidate/attachments/attachment/download", expect.objectContaining({ credentials: "same-origin" }));
    expect(write).toHaveBeenCalledTimes(1);
    expect(await (write.mock.calls[0][0] as Blob).text()).toBe("server-file");
    expect(close).toHaveBeenCalledTimes(1);
    expect(result).toMatchObject({ outcome: "saved", handle });
  });

  it("cancellation does not download or report saved", async () => {
    picker.mockRejectedValue(new DOMException("cancel", "AbortError"));
    await expect(saveCandidateAttachmentWithPicker("c", "a", "form.pdf")).resolves.toMatchObject({ outcome: "cancelled" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(handle.createWritable).not.toHaveBeenCalled();
  });

  it("does not start writing when download fails", async () => {
    fetchMock.mockResolvedValue(Response.json({ detail: "Вложение не найдено" }, { status: 404 }));
    await expect(saveCandidateAttachmentWithPicker("c", "a", "form.pdf")).rejects.toThrow("Вложение не найдено");
    expect(handle.createWritable).not.toHaveBeenCalled();
  });

  it("aborts failed writes without returning success", async () => {
    write.mockRejectedValue(new Error("Disk full"));
    await expect(saveCandidateAttachmentWithPicker("c", "a", "form.docx")).rejects.toThrow("Disk full");
    expect(abort).toHaveBeenCalledTimes(1);
    expect(close).not.toHaveBeenCalled();
  });

  it("handles an unsupported picker without making a request", async () => {
    vi.stubGlobal("showSaveFilePicker", undefined);
    expect(saveDialogAvailable()).toBe(false);
    await expect(saveCandidateAttachmentWithPicker("c", "a", "form.pdf")).rejects.toThrow("недоступен");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("opens the local handle's bytes without a repeated download endpoint", async () => {
    vi.useFakeTimers();
    const localFile = new File(["locally-edited-file"], "local-form.docx");
    handle.getFile.mockResolvedValue(localFile);
    const tab = { opener: window, closed: false, location: { replace: vi.fn() }, close: vi.fn() };
    const open = vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    const createURL = vi.fn(() => "blob:local-file");
    const revokeURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL: createURL, revokeObjectURL: revokeURL });
    await openSavedFile(handle as unknown as FileSystemFileHandle);
    expect(open).toHaveBeenCalledWith("about:blank", "_blank");
    expect(tab.opener).toBeNull();
    expect(createURL).toHaveBeenCalledWith(localFile);
    expect(tab.location.replace).toHaveBeenCalledWith("blob:local-file");
    expect(fetchMock).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(revokeURL).toHaveBeenCalledWith("blob:local-file");
  });

  it("reports popup blocking rather than claiming the file opened", async () => {
    vi.spyOn(window, "open").mockReturnValue(null);
    await expect(openSavedFile(handle as unknown as FileSystemFileHandle)).rejects.toThrow("заблокировал");
    expect(handle.getFile).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("closes the reserved tab if local-file access fails", async () => {
    const tab = { opener: window, close: vi.fn() };
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    handle.getFile.mockRejectedValue(new Error("Permission revoked"));
    await expect(openSavedFile(handle as unknown as FileSystemFileHandle)).rejects.toThrow("Permission revoked");
    expect(tab.close).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
