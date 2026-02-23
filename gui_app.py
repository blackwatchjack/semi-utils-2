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

from PIL import Image
from PIL import ImageTk

from engine import get_config_spec
from engine import process_images
from logging_setup import setup_temp_logging
from ui_visibility import POSITIONS
from ui_visibility import sanitize_config


def should_fallback_to_web(system: str, mac_major: int, tk_version: float) -> bool:
    # Tk 8.5 on newer macOS can crash at Tk() initialization.
    return system == "Darwin" and mac_major >= 15 and tk_version < 8.6


def run_safe_web_fallback(host: str = "127.0.0.1", port: int = 8765) -> None:
    from web_gui_app import run_server
    from web_gui_app import start_server_background

    try:
        import webview  # type: ignore
    except Exception:
        run_server(host, port, open_browser=True)
        return

    server = None
    server_thread: threading.Thread | None = None
    try:
        server, url, log_path = start_server_background(host, port)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        print(f"Web GUI embedded at {url}")
        print(f"Runtime log file: {log_path}")

        webview.create_window("semi-utils 网页界面", url, width=1400, height=900, min_size=(1100, 760))
        webview.start(gui="cocoa")
    except Exception as exc:
        print(f"Embedded webview unavailable, fallback to browser mode: {exc}")
        if server is not None:
            server.shutdown()
            server.server_close()
        run_server(host, port, open_browser=True)
    finally:
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        if server_thread is not None and server_thread.is_alive():
            server_thread.join(timeout=1.0)


class SemiUtilsGuiApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("semi-utils 图形界面")
        self.root.geometry("1480x920")
        self.root.minsize(1160, 760)

        self.spec = get_config_spec()
        self.defaults = copy.deepcopy(self.spec["defaults"])
        self.layout_options = self.spec["enums"]["layout_type"]
        self.element_options = self.spec["enums"]["element_name"]
        self.logo_position_options = self.spec["enums"]["logo_position"]
        self.font_size_options = self.spec["enums"]["font_size_level"]

        self.layout_value_to_label = {
            str(item["value"]): self._display_label("layout_type", item)
            for item in self.layout_options
        }
        self.layout_label_to_value = {label: value for value, label in self.layout_value_to_label.items()}
        self.layout_labels = [self.layout_value_to_label[str(item["value"])] for item in self.layout_options]

        self.element_value_to_label = {
            str(item["value"]): self._display_label("element_name", item)
            for item in self.element_options
        }
        self.element_label_to_value = {label: value for value, label in self.element_value_to_label.items()}
        self.element_labels = [self.element_value_to_label[str(item["value"])] for item in self.element_options]

        self.logo_position_value_to_label = {
            str(item["value"]): self._display_label("logo_position", item)
            for item in self.logo_position_options
        }
        self.logo_position_label_to_value = {
            label: value for value, label in self.logo_position_value_to_label.items()
        }
        self.logo_position_labels = [
            self.logo_position_value_to_label[str(item["value"])] for item in self.logo_position_options
        ]

        self.input_paths: list[Path] = []
        self.result_paths: list[Path | None] = []
        self.path_to_index: dict[Path, int] = {}
        self.expected_output_map: dict[Path, Path] = {}

        self._thumb_refs: list[ImageTk.PhotoImage] = []
        self._preview_image_ref: ImageTk.PhotoImage | None = None

        self.event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self._syncing_visibility = False
        self._field_visibility: dict[str, bool] = {}
        self._group_rows: dict[str, list[tuple[str | None, ttk.Frame]]] = {}

        self.current_preview_index = -1
        self.active_preview_mode = False

        self._build_variables()
        self._build_ui()
        self._bind_visibility_events()
        self._apply_visibility_rules()
        self._refresh_control_state()
        self.root.after(100, self._drain_events)

    @staticmethod
    def _display_label(option_type: str, item: dict[str, Any]) -> str:
        value = str(item["value"])
        label = str(item.get("label", value))
        if option_type == "logo_position":
            if value == "left":
                return "左侧"
            if value == "right":
                return "右侧"
        return label

    def _build_variables(self) -> None:
        layout_default = self.defaults["layout"]["type"]
        logo_position_default = self.defaults["layout"]["logo_position"]

        self.layout_var = tk.StringVar(value=self.layout_value_to_label.get(layout_default, layout_default))
        self.output_dir_var = tk.StringVar(value="")
        self.quality_var = tk.IntVar(value=self.defaults["base"]["quality"])
        self.background_color_var = tk.StringVar(value=self.defaults["layout"]["background_color"])

        self.logo_enable_var = tk.BooleanVar(value=self.defaults["layout"]["logo_enable"])
        self.logo_position_var = tk.StringVar(
            value=self.logo_position_value_to_label.get(logo_position_default, logo_position_default)
        )

        self.shadow_var = tk.BooleanVar(value=self.defaults["global"]["shadow"]["enable"])
        self.white_margin_var = tk.BooleanVar(value=self.defaults["global"]["white_margin"]["enable"])
        self.white_margin_width_var = tk.IntVar(value=self.defaults["global"]["white_margin"]["width"])
        self.padding_ratio_var = tk.BooleanVar(value=self.defaults["global"]["padding_with_original_ratio"]["enable"])
        self.equivalent_focal_length_var = tk.BooleanVar(
            value=self.defaults["global"]["focal_length"]["use_equivalent_focal_length"]
        )

        self.font_size_var = tk.IntVar(value=self.defaults["base"]["font_size"])
        self.bold_font_size_var = tk.IntVar(value=self.defaults["base"]["bold_font_size"])
        self.font_path_var = tk.StringVar(value=self.defaults["base"]["font"])
        self.bold_font_path_var = tk.StringVar(value=self.defaults["base"]["bold_font"])
        self.alt_font_path_var = tk.StringVar(value=self.defaults["base"]["alternative_font"])
        self.alt_bold_font_path_var = tk.StringVar(value=self.defaults["base"]["alternative_bold_font"])

        self.preview_var = tk.BooleanVar(value=False)
        self.preview_max_size_var = tk.IntVar(value=1600)
        self.preview_quality_var = tk.IntVar(value=80)

        self.element_name_vars: dict[str, tk.StringVar] = {}
        self.element_value_vars: dict[str, tk.StringVar] = {}
        self.element_color_vars: dict[str, tk.StringVar] = {}
        self.element_bold_vars: dict[str, tk.BooleanVar] = {}
        for position in POSITIONS:
            element = self.defaults["layout"]["elements"][position]
            default_name = element["name"]
            self.element_name_vars[position] = tk.StringVar(
                value=self.element_value_to_label.get(default_name, default_name)
            )
            self.element_value_vars[position] = tk.StringVar(value=element.get("value", ""))
            self.element_color_vars[position] = tk.StringVar(value=element.get("color", "#212121"))
            self.element_bold_vars[position] = tk.BooleanVar(value=element.get("is_bold", False))

        self.preview_zoom_var = tk.StringVar(value="FIT")
        self.status_var = tk.StringVar(value="就绪")
        self.current_file_var = tk.StringVar(value="当前文件：-")

    def _build_ui(self) -> None:
        root_container = ttk.Frame(self.root, padding=10)
        root_container.pack(fill=tk.BOTH, expand=True)

        main_frame = ttk.Frame(root_container)
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.columnconfigure(0, weight=0)
        main_frame.columnconfigure(1, weight=1)
        main_frame.columnconfigure(2, weight=0)
        main_frame.rowconfigure(0, weight=1)

        self._build_left_panel(main_frame)
        self._build_center_panel(main_frame)
        self._build_right_panel(main_frame)

        self._build_bottom_bar(root_container)

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="图片与缩略图")
        panel.grid(row=0, column=0, sticky=tk.NS, padx=(0, 8))
        panel.configure(width=300)

        toolbar = ttk.Frame(panel)
        toolbar.pack(fill=tk.X, padx=8, pady=8)

        self.add_btn = ttk.Button(toolbar, text="上传", command=self._add_files)
        self.add_btn.pack(side=tk.LEFT)
        self.remove_btn = ttk.Button(toolbar, text="移除", command=self._remove_selected_input)
        self.remove_btn.pack(side=tk.LEFT, padx=6)
        self.clear_btn = ttk.Button(toolbar, text="清空", command=self._clear_inputs)
        self.clear_btn.pack(side=tk.LEFT)

        list_frame = ttk.Frame(panel)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        y_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        self.input_tree = ttk.Treeview(
            list_frame,
            show="tree",
            selectmode="browse",
            yscrollcommand=y_scroll.set,
            height=18,
        )
        y_scroll.config(command=self.input_tree.yview)
        self.input_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.input_tree.bind("<<TreeviewSelect>>", self._on_select_input)

    def _build_center_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="处理后预览")
        panel.grid(row=0, column=1, sticky=tk.NSEW, padx=4)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(panel)
        toolbar.grid(row=0, column=0, sticky=tk.EW, padx=8, pady=8)

        self.prev_btn = ttk.Button(toolbar, text="上一张", command=self._show_prev_preview)
        self.prev_btn.pack(side=tk.LEFT)
        self.next_btn = ttk.Button(toolbar, text="下一张", command=self._show_next_preview)
        self.next_btn.pack(side=tk.LEFT, padx=6)

        ttk.Label(toolbar, text="缩放").pack(side=tk.LEFT, padx=(14, 6))
        self.zoom_combo = ttk.Combobox(
            toolbar,
            width=8,
            state="readonly",
            values=["FIT", "100%"],
            textvariable=self.preview_zoom_var,
        )
        self.zoom_combo.pack(side=tk.LEFT)
        self.zoom_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_preview_image())

        stage = ttk.Frame(panel)
        stage.grid(row=1, column=0, sticky=tk.NSEW, padx=8, pady=(0, 8))
        stage.columnconfigure(0, weight=1)
        stage.rowconfigure(0, weight=1)

        self.preview_canvas = tk.Canvas(stage, bg="#f3f6fb", highlightthickness=0)
        self.preview_canvas.grid(row=0, column=0, sticky=tk.NSEW)

        y_scroll = ttk.Scrollbar(stage, orient=tk.VERTICAL, command=self.preview_canvas.yview)
        y_scroll.grid(row=0, column=1, sticky=tk.NS)
        x_scroll = ttk.Scrollbar(stage, orient=tk.HORIZONTAL, command=self.preview_canvas.xview)
        x_scroll.grid(row=1, column=0, sticky=tk.EW)
        self.preview_canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        self.preview_canvas.bind("<Configure>", lambda _e: self._refresh_preview_image())

        self.preview_meta_label = ttk.Label(panel, text="未开始处理")
        self.preview_meta_label.grid(row=2, column=0, sticky=tk.W, padx=8, pady=(0, 8))

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="参数区")
        panel.grid(row=0, column=2, sticky=tk.NS, padx=(8, 0))
        panel.configure(width=420)

        scroll_container = ttk.Frame(panel)
        scroll_container.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        canvas = tk.Canvas(scroll_container, highlightthickness=0)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        y_scroll = ttk.Scrollbar(scroll_container, orient=tk.VERTICAL, command=canvas.yview)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.configure(yscrollcommand=y_scroll.set)

        self.param_content = ttk.Frame(canvas)
        self.param_window = canvas.create_window((0, 0), window=self.param_content, anchor=tk.NW)

        def _update_scrollregion(_event: tk.Event) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event: tk.Event) -> None:
            canvas.itemconfig(self.param_window, width=event.width)

        self.param_content.bind("<Configure>", _update_scrollregion)
        canvas.bind("<Configure>", _on_canvas_configure)

        self._group_rows = {
            "layout_output": [],
            "text": [],
            "global": [],
            "fonts": [],
        }

        self._build_layout_output_group(self.param_content)
        self._build_text_group(self.param_content)
        self._build_global_group(self.param_content)
        self._build_fonts_group(self.param_content)

        action_frame = ttk.Frame(panel)
        action_frame.pack(fill=tk.X, padx=8, pady=(6, 8))

        self.start_button = ttk.Button(action_frame, text="开始处理", command=self._start_processing)
        self.start_button.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _new_row(self, group_key: str, parent: ttk.LabelFrame, path: str | None = None) -> ttk.Frame:
        row = ttk.Frame(parent)
        self._group_rows[group_key].append((path, row))
        return row

    def _build_layout_output_group(self, parent: ttk.Frame) -> None:
        group = ttk.LabelFrame(parent, text="布局与输出")
        group.pack(fill=tk.X, padx=6, pady=5)

        row = self._new_row("layout_output", group, "layout.type")
        ttk.Label(row, text="布局", width=16).pack(side=tk.LEFT)
        self.layout_combo = ttk.Combobox(row, state="readonly", values=self.layout_labels, textvariable=self.layout_var)
        self.layout_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)

        row = self._new_row("layout_output", group, "base.quality")
        ttk.Label(row, text="画质", width=16).pack(side=tk.LEFT)
        self.quality_spin = ttk.Spinbox(row, from_=1, to=100, width=10, textvariable=self.quality_var)
        self.quality_spin.pack(side=tk.LEFT)

        row = self._new_row("layout_output", group, "layout.background_color")
        ttk.Label(row, text="背景颜色", width=16).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.background_color_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        row = self._new_row("layout_output", group, "layout.logo_enable")
        self.logo_enable_check = ttk.Checkbutton(row, text="启用徽标", variable=self.logo_enable_var)
        self.logo_enable_check.pack(side=tk.LEFT)

        row = self._new_row("layout_output", group, "layout.logo_position")
        ttk.Label(row, text="徽标位置", width=16).pack(side=tk.LEFT)
        self.logo_position_combo = ttk.Combobox(
            row,
            state="readonly",
            values=self.logo_position_labels,
            textvariable=self.logo_position_var,
            width=12,
        )
        self.logo_position_combo.pack(side=tk.LEFT)

        row = self._new_row("layout_output", group, None)
        self.preview_check = ttk.Checkbutton(row, text="预览模式", variable=self.preview_var, command=self._refresh_control_state)
        self.preview_check.pack(side=tk.LEFT)

        row = self._new_row("layout_output", group, None)
        ttk.Label(row, text="预览最大边长", width=16).pack(side=tk.LEFT)
        self.preview_max_size_spin = ttk.Spinbox(
            row,
            from_=200,
            to=8000,
            increment=100,
            width=10,
            textvariable=self.preview_max_size_var,
        )
        self.preview_max_size_spin.pack(side=tk.LEFT)

        row = self._new_row("layout_output", group, None)
        ttk.Label(row, text="预览质量", width=16).pack(side=tk.LEFT)
        self.preview_quality_spin = ttk.Spinbox(row, from_=1, to=100, width=10, textvariable=self.preview_quality_var)
        self.preview_quality_spin.pack(side=tk.LEFT)

        row = self._new_row("layout_output", group, None)
        ttk.Label(row, text="输出目录", width=16).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.output_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="浏览", command=self._select_output_dir).pack(side=tk.LEFT, padx=(6, 0))

    def _build_text_group(self, parent: ttk.Frame) -> None:
        group = ttk.LabelFrame(parent, text="文字参数")
        group.pack(fill=tk.X, padx=6, pady=5)

        labels = {
            "left_top": "左上",
            "left_bottom": "左下",
            "right_top": "右上",
            "right_bottom": "右下",
        }

        for position in POSITIONS:
            label = labels[position]

            row = self._new_row("text", group, f"layout.elements.{position}.name")
            ttk.Label(row, text=f"{label}元素", width=16).pack(side=tk.LEFT)
            combo = ttk.Combobox(
                row,
                state="readonly",
                values=self.element_labels,
                textvariable=self.element_name_vars[position],
            )
            combo.pack(side=tk.LEFT, fill=tk.X, expand=True)

            row = self._new_row("text", group, f"layout.elements.{position}.value")
            ttk.Label(row, text=f"{label}自定义", width=16).pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=self.element_value_vars[position]).pack(side=tk.LEFT, fill=tk.X, expand=True)

            row = self._new_row("text", group, f"layout.elements.{position}.color")
            ttk.Label(row, text=f"{label}颜色", width=16).pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=self.element_color_vars[position]).pack(side=tk.LEFT, fill=tk.X, expand=True)

            row = self._new_row("text", group, f"layout.elements.{position}.is_bold")
            ttk.Checkbutton(row, text=f"{label}加粗", variable=self.element_bold_vars[position]).pack(side=tk.LEFT)

    def _build_global_group(self, parent: ttk.Frame) -> None:
        group = ttk.LabelFrame(parent, text="全局效果")
        group.pack(fill=tk.X, padx=6, pady=5)

        row = self._new_row("global", group, "global.shadow.enable")
        ttk.Checkbutton(row, text="阴影", variable=self.shadow_var).pack(side=tk.LEFT)

        row = self._new_row("global", group, "global.white_margin.enable")
        ttk.Checkbutton(row, text="白边", variable=self.white_margin_var).pack(side=tk.LEFT)

        row = self._new_row("global", group, "global.white_margin.width")
        ttk.Label(row, text="白边宽度(%)", width=16).pack(side=tk.LEFT)
        self.white_margin_spin = ttk.Spinbox(row, from_=0, to=30, width=10, textvariable=self.white_margin_width_var)
        self.white_margin_spin.pack(side=tk.LEFT)

        row = self._new_row("global", group, "global.padding_with_original_ratio.enable")
        ttk.Checkbutton(row, text="按原图比例填充", variable=self.padding_ratio_var).pack(side=tk.LEFT)

        row = self._new_row("global", group, "global.focal_length.use_equivalent_focal_length")
        ttk.Checkbutton(row, text="使用等效焦距", variable=self.equivalent_focal_length_var).pack(side=tk.LEFT)

    def _build_fonts_group(self, parent: ttk.Frame) -> None:
        group = ttk.LabelFrame(parent, text="字体设置")
        group.pack(fill=tk.X, padx=6, pady=5)

        row = self._new_row("fonts", group, "base.font_size")
        ttk.Label(row, text="字体大小", width=16).pack(side=tk.LEFT)
        ttk.Combobox(
            row,
            state="readonly",
            values=[item["value"] for item in self.font_size_options],
            textvariable=self.font_size_var,
            width=10,
        ).pack(side=tk.LEFT)

        row = self._new_row("fonts", group, "base.bold_font_size")
        ttk.Label(row, text="加粗字体大小", width=16).pack(side=tk.LEFT)
        ttk.Combobox(
            row,
            state="readonly",
            values=[item["value"] for item in self.font_size_options],
            textvariable=self.bold_font_size_var,
            width=10,
        ).pack(side=tk.LEFT)

        row = self._new_row("fonts", group, "base.font")
        ttk.Label(row, text="字体路径", width=16).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.font_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        row = self._new_row("fonts", group, "base.bold_font")
        ttk.Label(row, text="加粗字体路径", width=16).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.bold_font_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        row = self._new_row("fonts", group, "base.alternative_font")
        ttk.Label(row, text="备用字体路径", width=16).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.alt_font_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        row = self._new_row("fonts", group, "base.alternative_bold_font")
        ttk.Label(row, text="备用加粗字体路径", width=16).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.alt_bold_font_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _build_bottom_bar(self, parent: ttk.Frame) -> None:
        bottom = ttk.Frame(parent)
        bottom.pack(fill=tk.X, pady=(8, 0))
        bottom.columnconfigure(0, weight=1)
        bottom.columnconfigure(1, weight=1)

        left = ttk.LabelFrame(bottom, text="状态 / 进度 / 当前文件")
        left.grid(row=0, column=0, sticky=tk.EW, padx=(0, 6))

        ttk.Label(left, textvariable=self.status_var, anchor=tk.W).pack(fill=tk.X, padx=8, pady=(8, 4))
        self.progress_bar = ttk.Progressbar(left, orient=tk.HORIZONTAL, mode="determinate", maximum=100)
        self.progress_bar.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(left, textvariable=self.current_file_var, anchor=tk.W).pack(fill=tk.X, padx=8, pady=(4, 8))

        right = ttk.LabelFrame(bottom, text="说明 / 错误汇总 / 操作提示")
        right.grid(row=0, column=1, sticky=tk.EW, padx=(6, 0))

        self.log_text = tk.Text(right, height=7, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    def _bind_visibility_events(self) -> None:
        trigger_vars: list[tk.Variable] = [
            self.layout_var,
            self.logo_enable_var,
            self.white_margin_var,
        ]
        trigger_vars.extend(self.element_name_vars.values())

        for var in trigger_vars:
            var.trace_add("write", self._on_visibility_trigger)

    def _on_visibility_trigger(self, *_args: Any) -> None:
        self._apply_visibility_rules()

    def _collect_config_data(self) -> dict[str, Any]:
        config_data = copy.deepcopy(self.defaults)

        layout_label = self.layout_var.get()
        config_data["layout"]["type"] = self.layout_label_to_value.get(layout_label, layout_label)
        config_data["layout"]["background_color"] = self.background_color_var.get().strip()
        config_data["layout"]["logo_enable"] = bool(self.logo_enable_var.get())

        logo_label = self.logo_position_var.get()
        config_data["layout"]["logo_position"] = self.logo_position_label_to_value.get(logo_label, logo_label)

        config_data["base"]["quality"] = int(self.quality_var.get())
        config_data["base"]["font_size"] = int(self.font_size_var.get())
        config_data["base"]["bold_font_size"] = int(self.bold_font_size_var.get())
        config_data["base"]["font"] = self.font_path_var.get().strip()
        config_data["base"]["bold_font"] = self.bold_font_path_var.get().strip()
        config_data["base"]["alternative_font"] = self.alt_font_path_var.get().strip()
        config_data["base"]["alternative_bold_font"] = self.alt_bold_font_path_var.get().strip()

        config_data["global"]["shadow"]["enable"] = bool(self.shadow_var.get())
        config_data["global"]["white_margin"]["enable"] = bool(self.white_margin_var.get())
        config_data["global"]["white_margin"]["width"] = int(self.white_margin_width_var.get())
        config_data["global"]["padding_with_original_ratio"]["enable"] = bool(self.padding_ratio_var.get())
        config_data["global"]["focal_length"]["use_equivalent_focal_length"] = bool(self.equivalent_focal_length_var.get())

        for position in POSITIONS:
            element_label = self.element_name_vars[position].get()
            element_name = self.element_label_to_value.get(element_label, element_label)
            element = config_data["layout"]["elements"][position]
            element["name"] = element_name
            element["color"] = self.element_color_vars[position].get().strip()
            element["is_bold"] = bool(self.element_bold_vars[position].get())
            element["value"] = self.element_value_vars[position].get()

        return config_data

    def _apply_config_to_vars(self, config_data: dict[str, Any]) -> None:
        layout_value = config_data["layout"]["type"]
        self.layout_var.set(self.layout_value_to_label.get(layout_value, layout_value))
        self.quality_var.set(int(config_data["base"]["quality"]))
        self.background_color_var.set(config_data["layout"]["background_color"])

        self.logo_enable_var.set(bool(config_data["layout"]["logo_enable"]))
        logo_position_value = config_data["layout"]["logo_position"]
        self.logo_position_var.set(self.logo_position_value_to_label.get(logo_position_value, logo_position_value))

        self.shadow_var.set(bool(config_data["global"]["shadow"]["enable"]))
        self.white_margin_var.set(bool(config_data["global"]["white_margin"]["enable"]))
        self.white_margin_width_var.set(int(config_data["global"]["white_margin"]["width"]))
        self.padding_ratio_var.set(bool(config_data["global"]["padding_with_original_ratio"]["enable"]))
        self.equivalent_focal_length_var.set(bool(config_data["global"]["focal_length"]["use_equivalent_focal_length"]))

        self.font_size_var.set(int(config_data["base"]["font_size"]))
        self.bold_font_size_var.set(int(config_data["base"]["bold_font_size"]))
        self.font_path_var.set(config_data["base"]["font"])
        self.bold_font_path_var.set(config_data["base"]["bold_font"])
        self.alt_font_path_var.set(config_data["base"]["alternative_font"])
        self.alt_bold_font_path_var.set(config_data["base"]["alternative_bold_font"])

        for position in POSITIONS:
            element = config_data["layout"]["elements"][position]
            self.element_name_vars[position].set(self.element_value_to_label.get(element["name"], element["name"]))
            self.element_value_vars[position].set(element.get("value", ""))
            self.element_color_vars[position].set(element.get("color", "#212121"))
            self.element_bold_vars[position].set(bool(element.get("is_bold", False)))

    def _apply_visibility_rules(self) -> None:
        if self._syncing_visibility:
            return

        self._syncing_visibility = True
        try:
            raw_config = self._collect_config_data()
            sanitized, visibility = sanitize_config(raw_config, self.defaults)
            self._apply_config_to_vars(sanitized)
            self._field_visibility = visibility
            self._repack_visibility_rows()
            self._refresh_control_state()
        finally:
            self._syncing_visibility = False

    def _repack_visibility_rows(self) -> None:
        for rows in self._group_rows.values():
            for _path, row in rows:
                row.pack_forget()

            for path, row in rows:
                is_visible = True if path is None else bool(self._field_visibility.get(path, True))
                if is_visible:
                    row.pack(fill=tk.X, padx=6, pady=2)

    def _refresh_control_state(self) -> None:
        white_margin_visible = self._field_visibility.get("global.white_margin.width", True)
        white_margin_state = "normal" if white_margin_visible else "disabled"
        self.white_margin_spin.config(state=white_margin_state)

        logo_position_visible = self._field_visibility.get("layout.logo_position", True)
        logo_position_state = "readonly" if logo_position_visible else "disabled"
        self.logo_position_combo.config(state=logo_position_state)

        preview_state = "normal" if self.preview_var.get() else "disabled"
        self.preview_max_size_spin.config(state=preview_state)
        self.preview_quality_spin.config(state=preview_state)

        start_enabled = bool(self.input_paths) and not self._is_running()
        self.start_button.config(state=("normal" if start_enabled else "disabled"))

        has_inputs = bool(self.input_paths)
        self.remove_btn.config(state=("normal" if has_inputs else "disabled"))
        self.clear_btn.config(state=("normal" if has_inputs else "disabled"))

        self._update_nav_buttons()

    def _is_running(self) -> bool:
        return self.worker_thread is not None and self.worker_thread.is_alive()

    def _log(self, text: str) -> None:
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)

    def _add_files(self) -> None:
        selected = filedialog.askopenfilenames(
            title="选择图片",
            filetypes=[
                ("图片文件", "*.jpg *.jpeg *.png *.JPG *.JPEG *.PNG"),
                ("所有文件", "*.*"),
            ],
        )
        for raw in selected:
            path = Path(raw)
            if path not in self.input_paths:
                self.input_paths.append(path)

        if self.input_paths and self.current_preview_index < 0:
            self.current_preview_index = 0

        self.result_paths = [None] * len(self.input_paths)
        self.path_to_index = {path: idx for idx, path in enumerate(self.input_paths)}
        self._refresh_input_tree()
        self._refresh_preview_image()
        self._refresh_control_state()

    def _remove_selected_input(self) -> None:
        selection = self.input_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if index < 0 or index >= len(self.input_paths):
            return

        del self.input_paths[index]
        if index < len(self.result_paths):
            del self.result_paths[index]

        if self.current_preview_index >= len(self.input_paths):
            self.current_preview_index = len(self.input_paths) - 1

        self.path_to_index = {path: idx for idx, path in enumerate(self.input_paths)}
        self._refresh_input_tree()
        self._refresh_preview_image()
        self._refresh_control_state()

    def _clear_inputs(self) -> None:
        self.input_paths.clear()
        self.result_paths.clear()
        self.path_to_index.clear()
        self.current_preview_index = -1
        self._refresh_input_tree()
        self._refresh_preview_image()
        self._refresh_control_state()

    def _refresh_input_tree(self) -> None:
        for iid in self.input_tree.get_children():
            self.input_tree.delete(iid)

        self._thumb_refs.clear()
        for index, path in enumerate(self.input_paths):
            thumb = self._load_thumbnail(path)
            self._thumb_refs.append(thumb)
            self.input_tree.insert("", tk.END, iid=str(index), text=path.name, image=thumb)

        if 0 <= self.current_preview_index < len(self.input_paths):
            selected_iid = str(self.current_preview_index)
            self.input_tree.selection_set(selected_iid)
            self.input_tree.focus(selected_iid)

    def _load_thumbnail(self, path: Path) -> ImageTk.PhotoImage:
        try:
            with Image.open(path) as image:
                image = image.convert("RGB")
                image.thumbnail((68, 68), Image.LANCZOS)
                rendered = image.copy()
        except Exception:
            rendered = Image.new("RGB", (68, 68), color=(230, 235, 242))
        return ImageTk.PhotoImage(rendered)

    def _on_select_input(self, _event: tk.Event) -> None:
        selection = self.input_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if index < 0 or index >= len(self.input_paths):
            return
        self.current_preview_index = index
        self._refresh_preview_image()

    def _show_prev_preview(self) -> None:
        if self.current_preview_index <= 0:
            return
        self.current_preview_index -= 1
        self._sync_tree_selection()
        self._refresh_preview_image()

    def _show_next_preview(self) -> None:
        if self.current_preview_index < 0 or self.current_preview_index >= len(self.input_paths) - 1:
            return
        self.current_preview_index += 1
        self._sync_tree_selection()
        self._refresh_preview_image()

    def _sync_tree_selection(self) -> None:
        if 0 <= self.current_preview_index < len(self.input_paths):
            iid = str(self.current_preview_index)
            self.input_tree.selection_set(iid)
            self.input_tree.focus(iid)

    def _update_nav_buttons(self) -> None:
        total = len(self.input_paths)
        prev_enabled = total > 0 and self.current_preview_index > 0
        next_enabled = total > 0 and 0 <= self.current_preview_index < total - 1
        self.prev_btn.config(state=("normal" if prev_enabled else "disabled"))
        self.next_btn.config(state=("normal" if next_enabled else "disabled"))

    def _refresh_preview_image(self) -> None:
        self.preview_canvas.delete("all")
        total = len(self.input_paths)
        self._update_nav_buttons()

        if self.current_preview_index < 0 or self.current_preview_index >= total:
            self.preview_meta_label.config(text="请先上传图片")
            self.current_file_var.set("当前文件：-")
            self.preview_canvas.create_text(20, 20, anchor=tk.NW, text="处理后预览区", fill="#6b7a8d")
            return

        source_name = self.input_paths[self.current_preview_index].name
        self.current_file_var.set(f"当前文件：{source_name}")
        self.preview_meta_label.config(text=f"第 {self.current_preview_index + 1}/{total} 张")

        result_path = None
        if self.current_preview_index < len(self.result_paths):
            result_path = self.result_paths[self.current_preview_index]

        if not result_path or not Path(result_path).exists():
            self.preview_canvas.create_text(
                20,
                20,
                anchor=tk.NW,
                text="该图片处理结果暂不可用。\n请先开始处理或等待该张完成。",
                fill="#6b7a8d",
            )
            return

        try:
            with Image.open(result_path) as source_image:
                image = source_image.convert("RGB")
                canvas_w = max(1, self.preview_canvas.winfo_width())
                canvas_h = max(1, self.preview_canvas.winfo_height())

                if self.preview_zoom_var.get() == "FIT":
                    scale = min(canvas_w / image.width, canvas_h / image.height)
                    scale = max(scale, 0.01)
                    resized = image.resize(
                        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                        Image.LANCZOS,
                    )
                else:
                    resized = image.copy()

                self._preview_image_ref = ImageTk.PhotoImage(resized)
                image_w = self._preview_image_ref.width()
                image_h = self._preview_image_ref.height()
                x = max((canvas_w - image_w) // 2, 0)
                y = max((canvas_h - image_h) // 2, 0)
                self.preview_canvas.create_image(x, y, anchor=tk.NW, image=self._preview_image_ref)
                self.preview_canvas.configure(scrollregion=(0, 0, max(canvas_w, image_w), max(canvas_h, image_h)))
        except Exception as exc:
            self.preview_canvas.create_text(20, 20, anchor=tk.NW, text=f"预览加载失败: {exc}", fill="#b42318")

    def _select_output_dir(self) -> None:
        chosen = filedialog.askdirectory(title="选择输出目录")
        if chosen:
            self.output_dir_var.set(chosen)

    def _start_processing(self) -> None:
        if self._is_running():
            return
        if not self.input_paths:
            messagebox.showwarning("缺少输入", "请至少添加一张图片。")
            return

        try:
            raw_config = self._collect_config_data()
            config_data, _visibility = sanitize_config(raw_config, self.defaults)
            preview_mode = bool(self.preview_var.get())
            preview_max_size = int(self.preview_max_size_var.get()) if preview_mode else None
            preview_quality = int(self.preview_quality_var.get()) if preview_mode else None
            output_dir_raw = self.output_dir_var.get().strip()
            output_dir = output_dir_raw if output_dir_raw and not preview_mode else None
        except Exception as exc:
            messagebox.showerror("参数无效", f"参数配置错误：{exc}")
            return

        self.log_text.delete("1.0", tk.END)

        inputs = list(self.input_paths)
        self.result_paths = [None] * len(inputs)
        self.path_to_index = {path: idx for idx, path in enumerate(inputs)}
        self.expected_output_map = {}
        self.active_preview_mode = preview_mode

        if not preview_mode:
            output_base = Path(output_dir) if output_dir else None
            for path in inputs:
                if output_base is None:
                    self.expected_output_map[path] = path.with_name(path.name)
                else:
                    self.expected_output_map[path] = output_base / path.name

        if self.current_preview_index < 0 and inputs:
            self.current_preview_index = 0

        self.progress_bar["value"] = 0
        self.status_var.set(f"处理中 0/{len(inputs)}")
        self._log(f"开始处理，共 {len(inputs)} 张图片。")
        if preview_mode:
            self._log("已启用预览模式。")
        elif output_dir:
            self._log(f"输出目录：{output_dir}")
        else:
            self._log("未设置输出目录，输出将覆盖到原图同目录同名文件。")

        self.worker_thread = threading.Thread(
            target=self._run_process_worker,
            args=(inputs, config_data, output_dir, preview_mode, preview_max_size, preview_quality),
            daemon=True,
        )
        self.worker_thread.start()
        self._refresh_control_state()
        self._refresh_preview_image()

    def _run_process_worker(
        self,
        inputs: list[Path],
        config_data: dict[str, Any],
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
                self._log(f"[{current}/{total}] 成功：{source_path}")
                if not self.active_preview_mode:
                    index = self.path_to_index.get(Path(source_path))
                    output_path = self.expected_output_map.get(Path(source_path))
                    if index is not None and output_path is not None and output_path.exists():
                        self.result_paths[index] = output_path
            else:
                self._log(f"[{current}/{total}] 失败：{source_path} -> {error}")

            self.status_var.set(f"处理中 {current}/{total}")
            self._refresh_preview_image()

        elif kind == "error":
            _, source_path, exc = event
            self._log(f"错误回调：{source_path} -> {exc}")

        elif kind == "preview":
            _, source_path, preview_path = event
            index = self.path_to_index.get(Path(source_path))
            if index is not None:
                self.result_paths[index] = Path(preview_path)
            self._refresh_preview_image()

        elif kind == "done":
            _, errors = event
            self.worker_thread = None
            self.progress_bar["value"] = 100
            if errors:
                self.status_var.set(f"完成（{len(errors)} 个错误）")
                self._log(f"处理完成，出现 {len(errors)} 个错误。")
                messagebox.showwarning("处理完成", f"处理完成，但有 {len(errors)} 个错误。请查看日志。")
            else:
                self.status_var.set("完成")
                self._log("处理完成，全部成功。")
                messagebox.showinfo("处理完成", "全部图片处理成功。")
            self._refresh_control_state()
            self._refresh_preview_image()

        elif kind == "fatal":
            _, exc = event
            self.worker_thread = None
            self.status_var.set("失败")
            self._log(f"致命错误：{exc}")
            messagebox.showerror("致命错误", str(exc))
            self._refresh_control_state()
            self._refresh_preview_image()


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
            "检测到当前桌面模式的 Tk 运行时不兼容 "
            f"(macOS {mac_major}, Tk {tk_version})，切换到安全 Web 模式..."
        )
        run_safe_web_fallback("127.0.0.1", 8765)
        return

    log_path = setup_temp_logging(name_prefix="semi-utils-desktop", cleanup_on_exit=False)
    root = tk.Tk()
    app = SemiUtilsGuiApp(root)
    app._log("GUI 已就绪。")
    app._log(f"运行日志文件：{log_path}")
    root.mainloop()


if __name__ == "__main__":
    main()
