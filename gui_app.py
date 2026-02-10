from __future__ import annotations

import copy
import os
import platform
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk
from typing import Any

from engine import get_config_spec
from engine import process_images
from enums.constant import CUSTOM_VALUE
from logging_setup import setup_temp_logging


def should_fallback_to_web(system: str, mac_major: int, tk_version: float) -> bool:
    # Tk 8.5 on newer macOS can crash at Tk() initialization.
    return system == "Darwin" and mac_major >= 15 and tk_version < 8.6


class SemiUtilsGuiApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("semi-utils GUI")
        self.root.geometry("1200x820")
        self.root.minsize(1000, 700)

        self.spec = get_config_spec()
        self.defaults = copy.deepcopy(self.spec["defaults"])
        self.layout_options = self.spec["enums"]["layout_type"]
        self.element_options = self.spec["enums"]["element_name"]
        self.logo_position_options = self.spec["enums"]["logo_position"]

        self.input_paths: list[Path] = []
        self.preview_paths: list[Path] = []
        self.event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None

        self._build_variables()
        self._build_ui()
        self._refresh_control_state()
        self.root.after(100, self._drain_events)

    def _build_variables(self) -> None:
        layout_default = self.defaults["layout"]["type"]
        logo_position_default = self.defaults["layout"]["logo_position"]

        self.layout_var = tk.StringVar(value=layout_default)
        self.output_dir_var = tk.StringVar(value="")
        self.quality_var = tk.IntVar(value=self.defaults["base"]["quality"])
        self.background_color_var = tk.StringVar(value=self.defaults["layout"]["background_color"])
        self.shadow_var = tk.BooleanVar(value=self.defaults["global"]["shadow"]["enable"])
        self.white_margin_var = tk.BooleanVar(value=self.defaults["global"]["white_margin"]["enable"])
        self.white_margin_width_var = tk.IntVar(value=self.defaults["global"]["white_margin"]["width"])
        self.padding_ratio_var = tk.BooleanVar(value=self.defaults["global"]["padding_with_original_ratio"]["enable"])
        self.equivalent_focal_length_var = tk.BooleanVar(
            value=self.defaults["global"]["focal_length"]["use_equivalent_focal_length"]
        )
        self.logo_enable_var = tk.BooleanVar(value=self.defaults["layout"]["logo_enable"])
        self.logo_position_var = tk.StringVar(value=logo_position_default)

        self.preview_var = tk.BooleanVar(value=False)
        self.preview_max_size_var = tk.IntVar(value=1600)
        self.preview_quality_var = tk.IntVar(value=80)

        self.element_name_vars: dict[str, tk.StringVar] = {}
        self.element_value_vars: dict[str, tk.StringVar] = {}
        for position in ("left_top", "left_bottom", "right_top", "right_bottom"):
            element = self.defaults["layout"]["elements"][position]
            self.element_name_vars[position] = tk.StringVar(value=element["name"])
            self.element_value_vars[position] = tk.StringVar(value=element.get("value", ""))

        self.progress_value_var = tk.DoubleVar(value=0)
        self.status_var = tk.StringVar(value="Ready")

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=10)
        container.pack(fill=tk.BOTH, expand=True)

        self._build_input_panel(container)
        self._build_config_panel(container)
        self._build_preview_panel(container)
        self._build_action_panel(container)
        self._build_log_panel(container)

    def _build_input_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Input")
        frame.pack(fill=tk.BOTH, expand=False, padx=4, pady=4)

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X, padx=8, pady=8)

        ttk.Button(buttons, text="Add Files", command=self._add_files).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Remove Selected", command=self._remove_selected_input).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Clear", command=self._clear_inputs).pack(side=tk.LEFT)

        ttk.Label(buttons, text="Output Dir").pack(side=tk.LEFT, padx=(16, 6))
        ttk.Entry(buttons, textvariable=self.output_dir_var, width=60).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(buttons, text="Browse", command=self._select_output_dir).pack(side=tk.LEFT, padx=6)

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        self.input_listbox = tk.Listbox(
            list_frame,
            height=7,
            selectmode=tk.EXTENDED,
            yscrollcommand=scrollbar.set,
            exportselection=False,
        )
        scrollbar.config(command=self.input_listbox.yview)
        self.input_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_config_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Config")
        frame.pack(fill=tk.BOTH, expand=False, padx=4, pady=4)

        grid = ttk.Frame(frame)
        grid.pack(fill=tk.X, padx=8, pady=8)
        for col in range(4):
            grid.columnconfigure(col, weight=1)

        ttk.Label(grid, text="Layout").grid(row=0, column=0, sticky=tk.W, padx=4, pady=4)
        self.layout_combo = ttk.Combobox(
            grid,
            width=28,
            state="readonly",
            values=[item["value"] for item in self.layout_options],
            textvariable=self.layout_var,
        )
        self.layout_combo.grid(row=0, column=1, sticky=tk.EW, padx=4, pady=4)

        ttk.Label(grid, text="Quality").grid(row=0, column=2, sticky=tk.W, padx=4, pady=4)
        self.quality_spin = ttk.Spinbox(grid, from_=1, to=100, width=8, textvariable=self.quality_var)
        self.quality_spin.grid(row=0, column=3, sticky=tk.W, padx=4, pady=4)

        ttk.Label(grid, text="Background Color").grid(row=1, column=0, sticky=tk.W, padx=4, pady=4)
        ttk.Entry(grid, textvariable=self.background_color_var, width=20).grid(
            row=1,
            column=1,
            sticky=tk.W,
            padx=4,
            pady=4,
        )

        ttk.Label(grid, text="Logo Position").grid(row=1, column=2, sticky=tk.W, padx=4, pady=4)
        self.logo_position_combo = ttk.Combobox(
            grid,
            width=12,
            state="readonly",
            values=[item["value"] for item in self.logo_position_options],
            textvariable=self.logo_position_var,
        )
        self.logo_position_combo.grid(row=1, column=3, sticky=tk.W, padx=4, pady=4)

        self.shadow_check = ttk.Checkbutton(grid, text="Shadow", variable=self.shadow_var)
        self.shadow_check.grid(row=2, column=0, sticky=tk.W, padx=4, pady=4)

        self.white_margin_check = ttk.Checkbutton(
            grid,
            text="White Margin",
            variable=self.white_margin_var,
            command=self._refresh_control_state,
        )
        self.white_margin_check.grid(row=2, column=1, sticky=tk.W, padx=4, pady=4)

        ttk.Label(grid, text="White Margin Width (%)").grid(row=2, column=2, sticky=tk.W, padx=4, pady=4)
        self.white_margin_spin = ttk.Spinbox(
            grid,
            from_=0,
            to=30,
            width=8,
            textvariable=self.white_margin_width_var,
        )
        self.white_margin_spin.grid(row=2, column=3, sticky=tk.W, padx=4, pady=4)

        self.logo_enable_check = ttk.Checkbutton(
            grid,
            text="Logo Enabled",
            variable=self.logo_enable_var,
            command=self._refresh_control_state,
        )
        self.logo_enable_check.grid(row=3, column=0, sticky=tk.W, padx=4, pady=4)

        self.padding_ratio_check = ttk.Checkbutton(
            grid,
            text="Padding With Original Ratio",
            variable=self.padding_ratio_var,
        )
        self.padding_ratio_check.grid(row=3, column=1, sticky=tk.W, padx=4, pady=4)

        self.eq_focal_check = ttk.Checkbutton(
            grid,
            text="Use Equivalent Focal Length",
            variable=self.equivalent_focal_length_var,
        )
        self.eq_focal_check.grid(row=3, column=2, sticky=tk.W, padx=4, pady=4)

        text_frame = ttk.LabelFrame(frame, text="Text Elements")
        text_frame.pack(fill=tk.X, padx=8, pady=(0, 8))

        for col in range(3):
            text_frame.columnconfigure(col, weight=1)

        positions = [
            ("left_top", "Left Top"),
            ("left_bottom", "Left Bottom"),
            ("right_top", "Right Top"),
            ("right_bottom", "Right Bottom"),
        ]
        element_values = [item["value"] for item in self.element_options]
        for row, (position, label) in enumerate(positions):
            ttk.Label(text_frame, text=label).grid(row=row, column=0, sticky=tk.W, padx=4, pady=4)
            combo = ttk.Combobox(
                text_frame,
                state="readonly",
                values=element_values,
                textvariable=self.element_name_vars[position],
                width=26,
            )
            combo.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
            combo.bind("<<ComboboxSelected>>", lambda _event, p=position: self._refresh_custom_entry_state(p))

            entry = ttk.Entry(text_frame, textvariable=self.element_value_vars[position])
            entry.grid(row=row, column=2, sticky=tk.EW, padx=4, pady=4)
            setattr(self, f"custom_entry_{position}", entry)

        for position, _ in positions:
            self._refresh_custom_entry_state(position)

    def _build_preview_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Preview")
        frame.pack(fill=tk.BOTH, expand=False, padx=4, pady=4)

        options = ttk.Frame(frame)
        options.pack(fill=tk.X, padx=8, pady=8)
        for col in range(6):
            options.columnconfigure(col, weight=1)

        self.preview_check = ttk.Checkbutton(
            options,
            text="Enable Preview Mode",
            variable=self.preview_var,
            command=self._refresh_control_state,
        )
        self.preview_check.grid(row=0, column=0, sticky=tk.W, padx=4, pady=4)

        ttk.Label(options, text="Preview Max Size").grid(row=0, column=1, sticky=tk.W, padx=4, pady=4)
        self.preview_max_size_spin = ttk.Spinbox(
            options,
            from_=200,
            to=8000,
            increment=100,
            width=8,
            textvariable=self.preview_max_size_var,
        )
        self.preview_max_size_spin.grid(row=0, column=2, sticky=tk.W, padx=4, pady=4)

        ttk.Label(options, text="Preview Quality").grid(row=0, column=3, sticky=tk.W, padx=4, pady=4)
        self.preview_quality_spin = ttk.Spinbox(
            options,
            from_=1,
            to=100,
            width=8,
            textvariable=self.preview_quality_var,
        )
        self.preview_quality_spin.grid(row=0, column=4, sticky=tk.W, padx=4, pady=4)

        ttk.Button(options, text="Open Preview", command=self._open_selected_preview).grid(
            row=0,
            column=5,
            sticky=tk.E,
            padx=4,
            pady=4,
        )

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        self.preview_listbox = tk.Listbox(
            list_frame,
            height=5,
            selectmode=tk.SINGLE,
            yscrollcommand=scrollbar.set,
            exportselection=False,
        )
        scrollbar.config(command=self.preview_listbox.yview)
        self.preview_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview_listbox.bind("<Double-Button-1>", lambda _event: self._open_selected_preview())

    def _build_action_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, expand=False, padx=4, pady=4)

        self.start_button = ttk.Button(frame, text="Start", command=self._start_processing)
        self.start_button.pack(side=tk.LEFT)

        self.progress_bar = ttk.Progressbar(frame, orient=tk.HORIZONTAL, mode="determinate", maximum=100)
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)

        ttk.Label(frame, textvariable=self.status_var, width=34, anchor=tk.W).pack(side=tk.LEFT)

    def _build_log_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Log")
        frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        self.log_text = tk.Text(list_frame, height=10, wrap=tk.WORD, yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.log_text.yview)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _log(self, text: str) -> None:
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)

    def _add_files(self) -> None:
        selected = filedialog.askopenfilenames(
            title="Select images",
            filetypes=[
                ("Image files", "*.jpg *.jpeg *.png *.JPG *.JPEG *.PNG"),
                ("All files", "*.*"),
            ],
        )
        for raw in selected:
            path = Path(raw)
            if path not in self.input_paths:
                self.input_paths.append(path)
                self.input_listbox.insert(tk.END, str(path))
        self._refresh_control_state()

    def _remove_selected_input(self) -> None:
        selected_indices = list(self.input_listbox.curselection())
        if not selected_indices:
            return
        for index in reversed(selected_indices):
            del self.input_paths[index]
            self.input_listbox.delete(index)
        self._refresh_control_state()

    def _clear_inputs(self) -> None:
        self.input_paths.clear()
        self.input_listbox.delete(0, tk.END)
        self._refresh_control_state()

    def _select_output_dir(self) -> None:
        chosen = filedialog.askdirectory(title="Select output directory")
        if chosen:
            self.output_dir_var.set(chosen)

    def _refresh_custom_entry_state(self, position: str) -> None:
        entry: ttk.Entry = getattr(self, f"custom_entry_{position}")
        if self.element_name_vars[position].get() == CUSTOM_VALUE:
            entry.config(state="normal")
        else:
            entry.config(state="disabled")

    def _refresh_control_state(self) -> None:
        self.white_margin_spin.config(state=("normal" if self.white_margin_var.get() else "disabled"))
        self.logo_position_combo.config(state=("readonly" if self.logo_enable_var.get() else "disabled"))
        preview_state = "normal" if self.preview_var.get() else "disabled"
        self.preview_max_size_spin.config(state=preview_state)
        self.preview_quality_spin.config(state=preview_state)
        start_enabled = bool(self.input_paths) and not self._is_running()
        self.start_button.config(state=("normal" if start_enabled else "disabled"))

    def _is_running(self) -> bool:
        return self.worker_thread is not None and self.worker_thread.is_alive()

    def _build_config_data(self) -> dict:
        config_data = copy.deepcopy(self.defaults)
        config_data["layout"]["type"] = self.layout_var.get()
        config_data["layout"]["background_color"] = self.background_color_var.get()
        config_data["layout"]["logo_enable"] = self.logo_enable_var.get()
        config_data["layout"]["logo_position"] = self.logo_position_var.get()

        config_data["global"]["shadow"]["enable"] = self.shadow_var.get()
        config_data["global"]["white_margin"]["enable"] = self.white_margin_var.get()
        config_data["global"]["white_margin"]["width"] = int(self.white_margin_width_var.get())
        config_data["global"]["padding_with_original_ratio"]["enable"] = self.padding_ratio_var.get()
        config_data["global"]["focal_length"]["use_equivalent_focal_length"] = self.equivalent_focal_length_var.get()
        config_data["base"]["quality"] = int(self.quality_var.get())

        for position in ("left_top", "left_bottom", "right_top", "right_bottom"):
            element_name = self.element_name_vars[position].get()
            element = config_data["layout"]["elements"][position]
            element["name"] = element_name
            if element_name == CUSTOM_VALUE:
                element["value"] = self.element_value_vars[position].get()
            elif "value" in element:
                element.pop("value")

        return config_data

    def _start_processing(self) -> None:
        if self._is_running():
            return
        if not self.input_paths:
            messagebox.showwarning("No Input", "Please add at least one image.")
            return

        try:
            config_data = self._build_config_data()
            preview_mode = self.preview_var.get()
            preview_max_size = int(self.preview_max_size_var.get()) if preview_mode else None
            preview_quality = int(self.preview_quality_var.get()) if preview_mode else None
            output_dir_raw = self.output_dir_var.get().strip()
            output_dir = output_dir_raw if output_dir_raw and not preview_mode else None
        except Exception as exc:
            messagebox.showerror("Invalid Config", f"Configuration error: {exc}")
            return

        self.preview_paths.clear()
        self.preview_listbox.delete(0, tk.END)
        self.log_text.delete("1.0", tk.END)
        self.progress_value_var.set(0)
        self.progress_bar["value"] = 0

        inputs = list(self.input_paths)
        self.status_var.set(f"Running 0/{len(inputs)}")
        self._log(f"Start processing. Total inputs: {len(inputs)}")
        if preview_mode:
            self._log("Preview mode enabled.")
        elif output_dir:
            self._log(f"Output dir: {output_dir}")
        else:
            self._log("Output dir not set. Files will be written next to source images.")

        self.worker_thread = threading.Thread(
            target=self._run_process_worker,
            args=(inputs, config_data, output_dir, preview_mode, preview_max_size, preview_quality),
            daemon=True,
        )
        self.worker_thread.start()
        self._refresh_control_state()

    def _run_process_worker(
        self,
        inputs: list[Path],
        config_data: dict,
        output_dir: str | None,
        preview_mode: bool,
        preview_max_size: int | None,
        preview_quality: int | None,
    ) -> None:
        try:
            errors = process_images(
                inputs=inputs,
                config_data=config_data,
                output_dir=output_dir,
                on_progress=self._on_progress,
                on_error=self._on_error,
                preview=preview_mode,
                preview_max_size=preview_max_size,
                preview_quality=preview_quality,
                on_preview=self._on_preview,
            )
            self.event_queue.put(("done", errors))
        except Exception as exc:
            self.event_queue.put(("fatal", exc))

    def _on_progress(self, current: int, total: int, source_path: Path, error: Exception | None) -> None:
        self.event_queue.put(("progress", current, total, source_path, error))

    def _on_error(self, source_path: Path, exc: Exception) -> None:
        self.event_queue.put(("error", source_path, exc))

    def _on_preview(self, source_path: Path, preview_path: Path) -> None:
        self.event_queue.put(("preview", source_path, preview_path))

    def _drain_events(self) -> None:
        try:
            while True:
                self._handle_event(self.event_queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _handle_event(self, event: tuple[str, Any]) -> None:
        kind = event[0]
        if kind == "progress":
            _, current, total, source_path, error = event
            progress = 0 if total == 0 else round(current / total * 100, 2)
            self.progress_bar["value"] = progress
            if error is None:
                self._log(f"[{current}/{total}] OK: {source_path}")
            else:
                self._log(f"[{current}/{total}] FAIL: {source_path} -> {error}")
            self.status_var.set(f"Running {current}/{total}")
        elif kind == "error":
            _, source_path, exc = event
            self._log(f"Error callback: {source_path} -> {exc}")
        elif kind == "preview":
            _, source_path, preview_path = event
            self.preview_paths.append(preview_path)
            self.preview_listbox.insert(tk.END, f"{source_path.name} -> {preview_path}")
        elif kind == "done":
            _, errors = event
            self.worker_thread = None
            self.progress_bar["value"] = 100
            if errors:
                self.status_var.set(f"Done with errors ({len(errors)})")
                self._log(f"Completed with {len(errors)} error(s).")
                messagebox.showwarning("Completed", f"Done with {len(errors)} error(s). Check logs for details.")
            else:
                self.status_var.set("Done")
                self._log("Completed successfully.")
                messagebox.showinfo("Completed", "All images processed successfully.")
            self._refresh_control_state()
        elif kind == "fatal":
            _, exc = event
            self.worker_thread = None
            self.status_var.set("Failed")
            self._log(f"Fatal error: {exc}")
            messagebox.showerror("Fatal Error", str(exc))
            self._refresh_control_state()

    def _open_selected_preview(self) -> None:
        selected = self.preview_listbox.curselection()
        if not selected:
            return
        preview_path = self.preview_paths[selected[0]]
        try:
            webbrowser.open(preview_path.resolve().as_uri())
        except Exception as exc:
            messagebox.showerror("Open Preview Failed", str(exc))


def main() -> None:
    os.chdir(Path(__file__).resolve().parent)
    tk_version = tk.TkVersion
    system = platform.system()
    mac_major = 0
    if system == "Darwin":
        mac_ver = platform.mac_ver()[0]
        try:
            mac_major = int(mac_ver.split(".")[0]) if mac_ver else 0
        except ValueError:
            mac_major = 0

    if should_fallback_to_web(system, mac_major, tk_version):
        print(
            "Detected unsupported Tk runtime for desktop mode "
            f"(macOS {mac_major}, Tk {tk_version}). Switching to web GUI..."
        )
        from web_gui_app import run_server

        run_server("127.0.0.1", 8765, open_browser=True)
        return

    log_path = setup_temp_logging(name_prefix="semi-utils-desktop")
    root = tk.Tk()
    app = SemiUtilsGuiApp(root)
    app._log("GUI is ready.")
    app._log(f"Runtime log file: {log_path}")
    root.mainloop()


if __name__ == "__main__":
    main()
