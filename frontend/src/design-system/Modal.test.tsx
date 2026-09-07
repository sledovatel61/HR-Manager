import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button } from "./components/Button";
import { Modal } from "./components/Modal";

describe("Modal accessibility", () => {
  it("exposes the dialog semantics and traps focus inside", async () => {
    render(
      <Modal open onClose={vi.fn()} title="Окно" description="Описание окна" footer={<Button>Действие</Button>}>
        <input aria-label="Поле внутри" />
      </Modal>
    );

    const dialog = screen.getByRole("dialog", { name: "Окно" });
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAccessibleDescription("Описание окна");

    // Focus moved into the dialog (the first focusable control).
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Закрыть окно" })).toHaveFocus()
    );

    // Tab stays inside the trap (focus lands on one of the dialog controls).
    await userEvent.tab();
    await userEvent.tab();
    await waitFor(() => {
      const inside = [
        screen.getByLabelText("Поле внутри"),
        screen.getByRole("button", { name: "Закрыть окно" }),
        screen.getByRole("button", { name: "Действие" }),
      ];
      expect(inside).toContain(document.activeElement as HTMLElement);
    });
  });

  it("closes on Escape and restores focus to the opener", async () => {
    function Harness() {
      return (
        <div>
          <button type="button">Открыть</button>
          <Modal open onClose={() => document.dispatchEvent(new Event("modal-closed"))} title="Окно">
            <button type="button">Внутри</button>
          </Modal>
        </div>
      );
    }
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Открыть" });
    opener.focus();
    expect(opener).toHaveFocus();

    await waitFor(() => expect(screen.getByRole("button", { name: "Внутри" })).toBeInTheDocument());
    await userEvent.keyboard("{Escape}");
    // The trap restores focus to the previously focused opener.
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("announces destructive dialogs as alertdialog", () => {
    render(
      <Modal open destructive onClose={vi.fn()} title="Удалить?">
        <p>Тело</p>
      </Modal>
    );
    expect(screen.getByRole("alertdialog", { name: "Удалить?" })).toBeInTheDocument();
  });
});
