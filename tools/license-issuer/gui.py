# -*- coding: utf-8 -*-
"""Simple Tkinter GUI for offline license issuer — owner PC, no heavy deps.

- Generate keypair
- Issue license with expiry and user limit
- Verify license

Private key stays ONLY with owner, never in git/installer/logs.
"""

import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from datetime import date, timedelta

try:
    from license_issuer import generate_keypair, issue_license, load_private_key_from_file, load_public_key_from_file, verify_license
except ImportError:
    # When run from different cwd
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from license_issuer import generate_keypair, issue_license, load_private_key_from_file, load_public_key_from_file, verify_license


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("HR Manager — License Issuer (owner only, offline)")
        self.geometry("720x560")

        self.private_key_hex = tk.StringVar()
        self.public_key_b64 = tk.StringVar()
        self.client_name = tk.StringVar(value="Пилот Марии")
        self.expires_at = tk.StringVar(value=(date.today() + timedelta(days=90)).isoformat())
        self.max_users = tk.StringVar(value="5")
        self.license_id = tk.StringVar(value="")

        self._build_ui()

    def _build_ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Tab 1: Keypair
        tab1 = ttk.Frame(nb)
        nb.add(tab1, text="1. Ключи")

        ttk.Label(tab1, text="Приватный ключ (СЕКРЕТНО, только у владельца!):").pack(anchor="w", padx=10, pady=(10,2))
        priv_frame = ttk.Frame(tab1)
        priv_frame.pack(fill=tk.X, padx=10)
        ttk.Entry(priv_frame, textvariable=self.private_key_hex, width=80, show="*").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(priv_frame, text="Показать", command=self._toggle_priv).pack(side=tk.LEFT, padx=5)

        ttk.Label(tab1, text="Публичный ключ (для сборки образа, base64 44 символа):").pack(anchor="w", padx=10, pady=(10,2))
        ttk.Entry(tab1, textvariable=self.public_key_b64, width=80).pack(fill=tk.X, padx=10)

        btn_frame = ttk.Frame(tab1)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        ttk.Button(btn_frame, text="Сгенерировать новую пару", command=self._gen_keypair).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Загрузить приватный ключ из файла", command=self._load_priv).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Загрузить публичный ключ", command=self._load_pub).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Сохранить ключи в папку", command=self._save_keys).pack(side=tk.LEFT, padx=5)

        ttk.Label(tab1, text="⚠️ Приватный ключ НИКОГДА не попадает в git, установщик, Docker, логи, диагностический архив.\nСделайте резервную копию в зашифрованном хранилище (VeraCrypt/BitLocker/зашифрованная флешка).", foreground="red").pack(anchor="w", padx=10, pady=10)
        ttk.Label(tab1, text="Публичный ключ скопируйте в infra/license/public_key.b64 перед сборкой пилотного образа.\nВ Secrets.psm1 он попадёт в HRM_LICENSE_PUBLIC_KEY → pilot.env").pack(anchor="w", padx=10)

        # Tab 2: Issue
        tab2 = ttk.Frame(nb)
        nb.add(tab2, text="2. Выпустить лицензию")

        form = ttk.Frame(tab2)
        form.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(form, text="Имя клиента/пилота:").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(form, textvariable=self.client_name, width=40).grid(row=0, column=1, sticky="w", pady=2)

        ttk.Label(form, text="Действует до (YYYY-MM-DD, включительно до 23:59:59 UTC):").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(form, textvariable=self.expires_at, width=20).grid(row=1, column=1, sticky="w", pady=2)

        ttk.Label(form, text="Лимит активных пользователей (1..1000, включает Марию):").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(form, textvariable=self.max_users, width=10).grid(row=2, column=1, sticky="w", pady=2)

        ttk.Label(form, text="License ID (UUID, пусто = сгенерировать):").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Entry(form, textvariable=self.license_id, width=40).grid(row=3, column=1, sticky="w", pady=2)

        ttk.Button(tab2, text="Выпустить и сохранить .hrmlicense", command=self._issue).pack(padx=10, pady=10, anchor="w")

        self.issue_result = tk.Text(tab2, height=12)
        self.issue_result.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Tab 3: Verify
        tab3 = ttk.Frame(nb)
        nb.add(tab3, text="3. Проверить")

        ttk.Button(tab3, text="Проверить лицензию файлом", command=self._verify).pack(padx=10, pady=10, anchor="w")
        self.verify_result = tk.Text(tab3, height=15)
        self.verify_result.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    def _toggle_priv(self):
        # Simple toggle: show/hide by recreating entry? We'll just show in messagebox for safety
        if self.private_key_hex.get():
            messagebox.showinfo("Приватный ключ", self.private_key_hex.get())

    def _gen_keypair(self):
        if self.private_key_hex.get():
            if not messagebox.askyesno("Подтвердите", "Перезаписать текущие ключи в памяти? Старые будут потеряны, если не сохранены."):
                return
        priv, pub = generate_keypair()
        self.private_key_hex.set(priv)
        self.public_key_b64.set(pub)
        messagebox.showinfo("Готово", "Ключи сгенерированы. Сохраните их в папку (кнопка ниже).")

    def _load_priv(self):
        path = filedialog.askopenfilename(title="Выберите файл приватного ключа", filetypes=[("Hex/B64", "*.hex *.b64 *.txt"), ("All", "*.*")])
        if not path:
            return
        try:
            hex_key = load_private_key_from_file(Path(path))
            self.private_key_hex.set(hex_key)
            messagebox.showinfo("Готово", f"Приватный ключ загружен из {path}")
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc))

    def _load_pub(self):
        path = filedialog.askopenfilename(title="Выберите файл публичного ключа", filetypes=[("B64", "*.b64 *.txt"), ("All", "*.*")])
        if not path:
            return
        try:
            b64 = load_public_key_from_file(Path(path))
            self.public_key_b64.set(b64)
            messagebox.showinfo("Готово", f"Публичный ключ загружен из {path}")
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc))

    def _save_keys(self):
        out_dir = filedialog.askdirectory(title="Выберите папку для сохранения ключей")
        if not out_dir:
            return
        out_path = Path(out_dir)
        try:
            (out_path / "private_key.hex").write_text(self.private_key_hex.get() + "\n", encoding="utf-8")
            (out_path / "public_key.b64").write_text(self.public_key_b64.get() + "\n", encoding="utf-8")
            messagebox.showinfo("Сохранено", f"Ключи сохранены в {out_dir}\n\nВАЖНО: private_key.hex — СЕКРЕТНО, только у владельца! Сделайте зашифрованную резервную копию.")
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc))

    def _issue(self):
        try:
            max_u = int(self.max_users.get())
            lic_id = self.license_id.get().strip() or None
            data = issue_license(
                client_name=self.client_name.get(),
                expires_at=self.expires_at.get().strip(),
                max_active_users=max_u,
                private_hex=self.private_key_hex.get().strip(),
                license_id=lic_id,
            )
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc))
            return

        out_file = filedialog.asksaveasfilename(
            title="Сохранить лицензию",
            defaultextension=".hrmlicense",
            filetypes=[("HRM License", "*.hrmlicense"), ("JSON", "*.json"), ("All", "*.*")],
            initialfile=f"{data['client_name'].replace(' ', '_')}_{data['expires_at']}.hrmlicense",
        )
        if not out_file:
            return
        try:
            Path(out_file).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.issue_result.delete("1.0", tk.END)
            self.issue_result.insert(tk.END, json.dumps(data, ensure_ascii=False, indent=2))
            messagebox.showinfo("Готово", f"Лицензия сохранена: {out_file}\n\nОтправьте файл Марии. Она загрузит его в UI: Настройки → Лицензия.")
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc))

    def _verify(self):
        lic_path = filedialog.askopenfilename(title="Выберите файл лицензии", filetypes=[("License", "*.hrmlicense *.json"), ("All", "*.*")])
        if not lic_path:
            return
        if not self.public_key_b64.get().strip():
            pub_path = filedialog.askopenfilename(title="Выберите файл публичного ключа", filetypes=[("B64", "*.b64 *.txt"), ("All", "*.*")])
            if not pub_path:
                return
            try:
                b64 = load_public_key_from_file(Path(pub_path))
                self.public_key_b64.set(b64)
            except Exception as exc:
                messagebox.showerror("Ошибка", str(exc))
                return
        try:
            data = json.loads(Path(lic_path).read_text(encoding="utf-8"))
            verify_license(data, self.public_key_b64.get().strip())
            self.verify_result.delete("1.0", tk.END)
            self.verify_result.insert(tk.END, f"Подпись корректна.\n{json.dumps(data, ensure_ascii=False, indent=2)}")
            messagebox.showinfo("Проверка", "Подпись корректна.")
        except Exception as exc:
            self.verify_result.delete("1.0", tk.END)
            self.verify_result.insert(tk.END, f"Ошибка: {exc}")
            messagebox.showerror("Ошибка проверки", str(exc))


if __name__ == "__main__":
    app = App()
    app.mainloop()
