import os
import json
import time
import threading

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from tkcalendar import DateEntry

from datetime import datetime, timedelta

import xalpha_tool

DEBUG_TIMING = False

# Grid layout: row 0 = headers, row 1+ = data (top info is outside the table)
DATA_GRID_OFFSET = 1
# Canvas 尚未布局完成时的可见行数回退值
FALLBACK_VISIBLE_ROWS = 8

# 固定列（到赎回份额）；其后为动态赎回分段列
BASE_HEADERS = [
    "日期",
    "净值",
    "申购金额",
    "到账份额",
    "申购成本/份",
    "预估利润",
    "至今涨幅",
    "买入份额",
    "卖出份额",
    "赎回份额",
]
BASE_COL_COUNT = len(BASE_HEADERS)
# 引入「赎回份额」之前的基础列数（到卖出份额为止）
PRE_REDEEM_COL_BASE = 9
_PHASE_OUT = 0
_PHASE_IN = 1


class TradeSheet(tk.Frame):
    def __init__(self, parent, headers, sheet_name):
        if DEBUG_TIMING:
            self._init_start = time.perf_counter()

        super().__init__(parent)

        # headers 参数兼容旧调用；实际以 BASE + 赎回档为准
        self.redeem_tiers = [dict(t) for t in xalpha_tool.DEFAULT_REDEEM_TIERS]
        self._all_redeem_tiers = [dict(t) for t in self.redeem_tiers]
        self._subscribe_actual_rate = None
        self._filter_lock = False
        self.sheet_name = sheet_name
        self.rows = []
        self.rt_price = 0
        self._header_labels = []
        self.headers = self._compose_headers(self.redeem_tiers)
        self._refresh_col_indices()
        self.selected_row_indices = set()
        self._anchor_row_index = None
        self._selection_from_click = False
        self._batch_loading = False
        self._full_rows_data = []
        self._hidden_rows_data = []
        self._history_collapsed = True
        self._refitting = False
        self._fit_after_id = None
        self._refresh_busy = False
        self._refresh_gen = 0
        self._tiers_loading = False
        self._legacy_without_redeem_col = False
        self.is_qdii_var = tk.BooleanVar(value=False)

        self._configure_style()

        os.makedirs("data", exist_ok=True)
        self.save_path = os.path.join("data", f"{sheet_name}.json")

        self.top_info_bar = tk.Frame(self)
        self.top_info_bar.pack(side="top", fill="x", padx=4, pady=(4, 2))

        # Buttons stay outside the scrollable grid to avoid row collisions
        self.button_bar = tk.Frame(self)
        self.button_bar.pack(side="bottom", fill="x", pady=(4, 2))

        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.table_frame = tk.Frame(self.canvas)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.table_frame, anchor="nw")

        self.table_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

        if DEBUG_TIMING:
            self._init_loaded = time.perf_counter()

        self.draw_top_info_row()
        self.draw_table()
        self.load_data()
        self.render_action_buttons()
        self.canvas.update_idletasks()

        if DEBUG_TIMING:
            self._init_loaded_done = time.perf_counter()
            print(f"数据加载耗时: {(self._init_loaded_done - self._init_loaded)*1000:.2f}ms")

        # Defer network/timers so the window can paint immediately
        self.after(0, self.update_clock)
        # Stagger price refresh across tabs to avoid a startup request stampede
        price_delay = 80 + (sum(ord(c) for c in sheet_name) % 17) * 120
        self.after(price_delay, self.update_price)
        # 有 6 位代码时后台拉赎回分段并换列；再补一轮「能算却未填」的申购字段
        if str(sheet_name).isdigit() and len(str(sheet_name)) == 6:
            self.after(price_delay + 40, self.reload_redeem_tiers_async)
            self.after(price_delay + 100, self._startup_fill_missing)

    def _compose_headers(self, redeem_tiers):
        """基础列 + 赎回档；申购金额标题附申购实际费率。"""
        base = list(BASE_HEADERS)
        if self._subscribe_actual_rate is not None:
            base[2] = f"申购金额（{float(self._subscribe_actual_rate):.2f}%）"
        else:
            base[2] = "申购金额"
        return base + [t["label"] for t in redeem_tiers]

    def preferred_window_width(self):
        """按表格实际需求宽度估算窗口（隐藏零份额赎回列后应收窄）。"""
        self.update_idletasks()
        table_w = int(self.table_frame.winfo_reqwidth() or 0)
        top_w = int(self.top_info_bar.winfo_reqwidth() or 0)
        # 以表格为主；顶栏不应单独把窗口撑得更宽
        content_w = max(table_w, min(top_w, table_w + 40) if table_w else top_w)
        # 滚动条 + 边距
        return max(980, content_w + 48)

    def _notify_window_fit(self):
        root = self.winfo_toplevel()
        fit = getattr(root, "fit_window_to_active_sheet", None)
        if callable(fit):
            try:
                root.after_idle(fit)
            except Exception:
                pass

    def _refresh_col_indices(self):
        def _find(prefix, exact=None):
            for i, h in enumerate(self.headers):
                if exact is not None and h == exact:
                    return i
                if h == prefix or str(h).startswith(prefix):
                    return i
            raise ValueError(prefix)

        self.profit_col_index = _find("预估利润")
        self.gain_col_index = _find("至今涨幅")
        self.buy_col_index = _find("买入份额")
        self.sell_col_index = _find("卖出份额")
        self.redeem_shares_col_index = _find("赎回份额")
        self.amount_col_index = 2
        self.credited_col_index = 3
        self.redeem_col_indices = list(range(BASE_COL_COUNT, len(self.headers)))

    def _is_redeem_col(self, col_index):
        return col_index >= BASE_COL_COUNT

    def _is_amount_header(self, header):
        return header == "申购金额" or str(header).startswith("申购金额")

    def _is_credited_header(self, header):
        return header == "到账份额" or str(header).startswith("到账份额")

    @staticmethod
    def _format_share_amount(value):
        try:
            n = float(value)
        except (TypeError, ValueError):
            return "-"
        if abs(n - round(n)) < 1e-9:
            return str(int(round(n)))
        return str(round(n, 2))

    def _make_redeem_cell(self, grid_row, col_index, raw="-"):
        cell = tk.Entry(
            self.table_frame,
            width=self._col_char_width(self.headers[col_index] if col_index < len(self.headers) else ""),
            justify="center",
            state="readonly",
            readonlybackground="white",
        )
        self._set_entry_value(cell, raw if raw not in (None, "") else "-")
        self._bind_row_cell(cell, self.headers[col_index] if col_index < len(self.headers) else "")
        cell.grid(row=grid_row, column=col_index, sticky="nsew")
        return cell

    def _on_frame_configure(self, event):
        # Only update scroll region — do not grow canvas to full table height
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.canvas_window, width=event.width)
        # 折叠态下按可视高度重算「更多交易」
        if self._history_collapsed and not self._batch_loading and not self._refitting:
            if self._fit_after_id is not None:
                try:
                    self.after_cancel(self._fit_after_id)
                except Exception:
                    pass
            self._fit_after_id = self.after(120, self._refit_history_to_viewport)

    def _bind_mousewheel(self, event):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, event):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _configure_style(self):
        self.style = ttk.Style()
        self.style.configure("TradeSheet.TButton", padding=2, font=("Arial", 9))

    def has_vertical_scroll(self):
        return self.canvas.yview() != (0.0, 1.0)

    def get_total_height(self):
        self.update_idletasks()
        return self.winfo_reqheight()

    def _text_char_units(self, text):
        """按默认字体把文字折成 Tk 的字符宽度（以 '0' 为 1），保证能画全。"""
        sample = "" if text is None else str(text)
        if not sample:
            return 4
        f = tkfont.nametofont("TkDefaultFont")
        zero = max(f.measure("0"), 1)
        px = f.measure(sample)
        return max(4, (px + zero - 1) // zero + 2)

    def _col_char_width(self, header):
        """列宽以该列表头完整显示为准；日期列同时容下 yyyy-mm-dd。"""
        width = self._text_char_units(header)
        if header in ("日期", "Date"):
            # DateEntry 右侧还有日历按钮，比纯文字更宽
            width = max(width, self._text_char_units("2026-10-09") + 3)
        return width

    def _col_min_pixels(self, header):
        f = tkfont.nametofont("TkDefaultFont")
        text = "" if header is None else str(header)
        px = f.measure(text)
        if header in ("日期", "Date"):
            px = max(px, f.measure("2026-10-09") + 22)
        return px + 16

    def draw_table(self):
        for lbl in self._header_labels:
            try:
                lbl.destroy()
            except Exception:
                pass
        self._header_labels = []
        # 不用 uniform：各列只跟自己的表头走，避免被最长标题撑成等宽
        for i in range(max(len(self.headers), 16)):
            minsize = self._col_min_pixels(self.headers[i]) if i < len(self.headers) else 0
            self.table_frame.columnconfigure(
                i,
                weight=0 if i >= len(self.headers) else 1,
                uniform="",
                minsize=minsize,
            )
        for i, header in enumerate(self.headers):
            lbl = tk.Label(
                self.table_frame,
                text=header,
                borderwidth=1,
                relief="solid",
                width=self._col_char_width(header),
                anchor="center",
            )
            lbl.grid(row=0, column=i, sticky="nsew")
            self._header_labels.append(lbl)

    def draw_top_info_row(self):
        # 基金名称 | 名称 | 当前时间 | 场内价格 | 价格 | 涨跌幅 | QDII
        # 不设 uniform，避免顶栏 7 格被最宽控件等宽撑开、拖大整窗
        for c in range(7):
            self.top_info_bar.columnconfigure(c, weight=0)
        self.top_info_bar.columnconfigure(1, weight=1)  # 基金名可伸展

        self.top_slot1 = tk.Label(
            self.top_info_bar, text="基金名称", width=7, anchor="center"
        )
        self.top_slot1.grid(row=0, column=0, sticky="nsw", padx=(0, 2))

        # 基金名：略窄，其余顶栏控件略宽
        self.top_slot2 = tk.Entry(
            self.top_info_bar, width=14, justify="center", fg="blue"
        )
        self.top_slot2.grid(row=0, column=1, sticky="nsew", padx=2)

        self.top_time_slot = tk.Entry(
            self.top_info_bar,
            width=16,
            justify="center",
            fg="#333333",
            state="readonly",
            readonlybackground=self.top_info_bar.cget("bg"),
        )
        self.top_time_slot.grid(row=0, column=2, sticky="nsw", padx=2)

        self.top_slot3 = tk.Label(
            self.top_info_bar, text="场内价格", width=9, anchor="center"
        )
        self.top_slot3.grid(row=0, column=3, sticky="nsw", padx=2)

        self.top_slot4 = tk.Entry(
            self.top_info_bar, width=10, justify="center", fg="blue"
        )
        self.top_slot4.grid(row=0, column=4, sticky="nsw", padx=2)

        self.top_slot5 = tk.Entry(
            self.top_info_bar, width=9, justify="center"
        )
        self.top_slot5.grid(row=0, column=5, sticky="nsw", padx=2)

        self.qdii_check = ttk.Checkbutton(
            self.top_info_bar,
            text="QDII（T+2）",
            variable=self.is_qdii_var,
            command=self._on_qdii_toggle,
        )
        self.qdii_check.grid(row=0, column=6, sticky="nsw", padx=(2, 0))

    def _on_qdii_toggle(self):
        self._recompute_lot_state()
        if not self._batch_loading:
            self.save_data()

    def _is_input_header(self, header):
        return header in (
            "Date",
            "日期",
            "Sub. in Currency",
            "买入份额",
            "卖出份额",
            "赎回份额",
        ) or self._is_amount_header(header)

    def _bind_row_cell(self, cell, header):
        cell.bind("<Button-1>", self._on_cell_button1, add="+")
        cell.bind("<FocusIn>", lambda e, w=cell: self.highlight_selected_row(w))
        if header in ("买入份额", "卖出份额", "赎回份额"):
            cell.bind("<Return>", self.handle_sell_return)
            cell.bind("<FocusOut>", self.handle_sell_return)
        elif self._is_input_header(header):
            cell.bind("<Return>", self.handle_entry_return)
            cell.bind("<FocusOut>", self.handle_entry_return)

    def _make_row_widgets(self, grid_row, prefill=None):
        row_widgets = []
        for col_index, header in enumerate(self.headers):
            col_w = self._col_char_width(header)
            if header in ("Date", "日期"):
                cell = DateEntry(
                    self.table_frame,
                    width=col_w,
                    date_pattern="yyyy-mm-dd",
                    justify="center",
                )
                default_day = self.latest_trading_day()
                if prefill and header in prefill:
                    try:
                        cell.set_date(prefill[header])
                    except Exception:
                        cell.set_date(default_day)
                else:
                    cell.set_date(default_day)
            elif header in ("Sub. in Currency",) or self._is_amount_header(header):
                cell = tk.Entry(self.table_frame, width=col_w, justify="center")
                raw = None
                if prefill:
                    if header in prefill:
                        raw = prefill[header]
                    else:
                        raw = prefill.get("申购金额")
                        if raw is None:
                            for k, v in prefill.items():
                                if str(k).startswith("申购金额"):
                                    raw = v
                                    break
                cell.insert(0, "0" if raw in (None, "") else raw)
            elif header in ("买入份额", "卖出份额", "赎回份额"):
                cell = tk.Entry(self.table_frame, width=col_w, justify="center")
                if prefill and header in prefill and str(prefill[header]).strip() not in ("", "-"):
                    cell.insert(0, prefill[header])
                else:
                    cell.insert(0, "0")
            elif header == "申购成本/份":
                cell = tk.Entry(self.table_frame, width=col_w, justify="center")
                raw = (prefill or {}).get(header, "-")
                cell.insert(0, self._format_cost_per_share(raw if raw not in (None, "") else "-"))
            elif self._is_redeem_col(col_index):
                raw = (prefill or {}).get(header, "-") if prefill else "-"
                cell = tk.Entry(
                    self.table_frame,
                    width=col_w,
                    justify="center",
                    state="readonly",
                    readonlybackground="white",
                )
                self._set_entry_value(cell, raw if raw not in (None, "") else "-")
            else:
                cell = tk.Entry(self.table_frame, width=col_w, justify="center")
                if prefill and header in prefill:
                    cell.insert(0, prefill[header])
                else:
                    cell.insert(0, "-")

            self._bind_row_cell(cell, header)
            cell.grid(row=grid_row, column=col_index, sticky="nsew")
            row_widgets.append(cell)
        return row_widgets

    def _set_entry_value(self, entry, value, readonly=False):
        was_readonly = str(entry.cget("state")) == "readonly"
        entry.configure(state="normal")
        entry.delete(0, tk.END)
        entry.insert(0, value)
        if readonly or was_readonly:
            entry.configure(state="readonly")

    def add_row_on_bottom(self, prefill=None):
        if prefill is None:
            prefill = {"日期": self.latest_trading_day().strftime("%Y-%m-%d")}

        grid_row = len(self.rows) + DATA_GRID_OFFSET
        row_widgets = self._make_row_widgets(grid_row, prefill=prefill)
        self.rows.append(row_widgets)

        if not self._batch_loading:
            self._fold_overflow_into_hidden()
            self.render_action_buttons()
            self._recompute_lot_state()
            self.save_data()
            self.canvas.yview_moveto(1.0)

    def add_row_on_top(self, prefill=None):
        if prefill is None:
            prefill = {
                "日期": self.default_date_for_top_insert().strftime("%Y-%m-%d")
            }

        # Shift existing data rows down by one grid index
        for i, existing_row in enumerate(self.rows):
            for widget in existing_row:
                widget.grid_configure(row=i + DATA_GRID_OFFSET + 1)

        row_widgets = self._make_row_widgets(DATA_GRID_OFFSET, prefill=prefill)
        self.rows.insert(0, row_widgets)

        if not self._batch_loading:
            self._fold_overflow_into_hidden()
            self.render_action_buttons()
            self._recompute_lot_state()
            self.save_data()
            self.canvas.yview_moveto(0.0)

    def handle_entry_return(self, event):
        if self._batch_loading or self._refitting:
            return
        widget = event.widget
        try:
            if not widget.winfo_exists():
                return
            grid_row = int(widget.grid_info()["row"])
        except (KeyError, TypeError, ValueError, tk.TclError):
            return
        self.update_result(grid_row)

    def handle_sell_return(self, event):
        if self._batch_loading or self._refitting:
            return
        widget = getattr(event, "widget", None)
        if widget is not None:
            try:
                if not widget.winfo_exists():
                    return
            except tk.TclError:
                return
        self._recompute_lot_state()
        if not self._batch_loading:
            self.save_data()

    def _row_index_of_widget(self, widget):
        for row_index, row in enumerate(self.rows):
            if widget in row:
                return row_index
        return None

    def _on_cell_button1(self, event):
        """单击选一行；Ctrl+单击切换；Shift+单击选范围。"""
        row_index = self._row_index_of_widget(event.widget)
        if row_index is None:
            return

        self._selection_from_click = True
        ctrl = bool(event.state & 0x4)
        shift = bool(event.state & 0x1)

        if shift and self._anchor_row_index is not None:
            lo, hi = sorted((self._anchor_row_index, row_index))
            self.selected_row_indices = set(range(lo, hi + 1))
        elif ctrl:
            if row_index in self.selected_row_indices:
                self.selected_row_indices.discard(row_index)
            else:
                self.selected_row_indices.add(row_index)
            self._anchor_row_index = row_index
        else:
            self.selected_row_indices = {row_index}
            self._anchor_row_index = row_index

        self._refresh_selection_highlight()

    def delete_selected_rows(self):
        if not self.selected_row_indices:
            print("请先选中要删除的行（单击选中，Ctrl 多选，Shift 连选）")
            return

        for idx in sorted(self.selected_row_indices, reverse=True):
            if 0 <= idx < len(self.rows):
                for widget in self.rows[idx]:
                    widget.destroy()
                del self.rows[idx]

        self.selected_row_indices.clear()
        self._anchor_row_index = None

        for i, row in enumerate(self.rows):
            for widget in row:
                info = widget.grid_info()
                widget.grid(
                    row=i + DATA_GRID_OFFSET,
                    column=info["column"],
                    sticky=info.get("sticky", "nsew"),
                )

        self._refresh_selection_highlight()
        if self._history_collapsed and self._hidden_rows_data:
            self._refit_history_to_viewport()
        self.render_action_buttons()
        self.update_profits()
        self._recompute_lot_state()
        self.save_data()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    # Backward-compatible alias
    def delete_selected_row(self):
        self.delete_selected_rows()

    def render_action_buttons(self, *_args):
        for child in self.button_bar.winfo_children():
            child.destroy()

        show_more = bool(self._hidden_rows_data)
        col_count = 5 if show_more else 4
        for c in range(col_count):
            self.button_bar.columnconfigure(c, weight=1, uniform="btn_cols")

        self.add_top_btn = ttk.Button(
            self.button_bar,
            text="↑ 顶部插入 ↑",
            command=self.add_row_on_top,
            style="TradeSheet.TButton",
        )
        self.add_bottom_btn = ttk.Button(
            self.button_bar,
            text="↓ 底部追加 ↓",
            command=self.add_row_on_bottom,
            style="TradeSheet.TButton",
        )
        self.delete_row_btn = ttk.Button(
            self.button_bar,
            text="✖ 删除选中行 ✖",
            command=self.delete_selected_rows,
            style="TradeSheet.TButton",
        )
        self.update_profits_btn = ttk.Button(
            self.button_bar,
            text="刷新利润/涨幅",
            command=self.refresh_derived_metrics,
            style="TradeSheet.TButton",
        )

        buttons = [
            self.add_top_btn,
            self.add_bottom_btn,
            self.delete_row_btn,
            self.update_profits_btn,
        ]
        if show_more:
            self.show_more_btn = ttk.Button(
                self.button_bar,
                text=f"查看更多交易（{len(self._hidden_rows_data)}）",
                command=self.show_more_history,
                style="TradeSheet.TButton",
            )
            buttons.append(self.show_more_btn)

        for col, btn in enumerate(buttons):
            btn.grid(row=0, column=col, sticky="ew", padx=2, pady=2)

    # Keep old name as alias for any leftover callers
    def render_add_button(self, row=None):
        self.render_action_buttons()

    def _date_str_from_cell(self, date_cell):
        try:
            if isinstance(date_cell, DateEntry):
                return date_cell.get_date().strftime("%Y-%m-%d")
            raw = date_cell.get().strip() if hasattr(date_cell, "get") else str(date_cell).strip()
            return raw[:10] if raw else ""
        except Exception:
            return ""

    def _parse_buy_money(self, raw):
        text = (raw or "").strip()
        if text in ("", "-"):
            return 0.0
        return float(text)

    def _write_cell(self, cell, value):
        if isinstance(cell, DateEntry):
            try:
                cell.set_date(value)
            except Exception:
                pass
            return
        was_readonly = str(cell.cget("state")) == "readonly"
        cell.configure(state="normal")
        cell.delete(0, tk.END)
        cell.insert(0, "" if value is None else str(value))
        if was_readonly:
            cell.configure(state="readonly")

    def _calc_subscription_fields(self, fund_code, date_str, buy_money_amount):
        """按申购金额计算净值/到账/成本/买入份额（走 xalpha 缓存，同代码只拉一次）。"""
        result = xalpha_tool.calc_subscription_fields(
            fund_code, date_str, buy_money_amount
        )
        if result.get("cost") is not None:
            result["cost"] = self._format_cost_per_share(result["cost"])
        return result

    def _calc_subscription_fields_local_clear(self, buy_money_amount):
        """金额≤0：只清空申购派生列（到账/成本），不动买入/卖出/赎回。"""
        if buy_money_amount > 0:
            return None
        return {
            "nav": None,
            "shares": "-",
            "cost": "-",
            "buy_shares": None,
            "error": None,
        }

    @staticmethod
    def _is_blank_share(value):
        text = str(value).strip() if value is not None else ""
        return text in ("", "-", "0")

    @staticmethod
    def _is_blank_cell(value):
        text = str(value).strip() if value is not None else ""
        return text in ("", "-", "None")

    def _subscribe_fields_incomplete(self, nav, shares, cost, money):
        """申购金额>0 时净值/到账/成本任一空；金额≤0 时仅缺净值（供涨幅）也算未填完。"""
        if money <= 0:
            return self._is_blank_cell(nav)
        return (
            self._is_blank_cell(nav)
            or self._is_blank_cell(shares)
            or self._is_blank_cell(cost)
        )

    def _maybe_fill_buy_shares(self, current_buy, suggested):
        """申购成功后：仅当买入份额为空/0 时用到账份额预填，不覆盖已有买入。"""
        if suggested is None:
            return None
        if self._is_blank_share(current_buy):
            return suggested
        return None

    @staticmethod
    def _format_3dp(value):
        """金额类字段固定 3 位小数；'-' / 空保持为 '-'。"""
        if value is None:
            return "-"
        text = str(value).strip()
        if text in ("", "-"):
            return "-"
        try:
            return f"{float(text):.3f}"
        except (TypeError, ValueError):
            return text

    @classmethod
    def _format_cost_per_share(cls, value):
        """申购成本/份固定显示为 3 位小数。"""
        return cls._format_3dp(value)

    def _set_market_price_display(self, value):
        """顶部场内价格：强制 3 位小数。"""
        text = self._format_3dp(value)
        self.top_slot4.delete(0, tk.END)
        if text != "-":
            self.top_slot4.insert(0, text)
            try:
                self.rt_price = float(text)
            except (TypeError, ValueError):
                pass
        else:
            self.top_slot4.insert(0, "-")

    def _reformat_all_cost_displays(self):
        """把界面与隐藏行里已有成本统一成 3 位小数（不依赖联网重算）。"""
        cost_i = 4
        for row in self.rows:
            if cost_i >= len(row):
                continue
            formatted = self._format_cost_per_share(row[cost_i].get())
            if formatted != row[cost_i].get():
                self._write_cell(row[cost_i], formatted)
        for row in self._hidden_rows_data:
            if len(row) > cost_i:
                row[cost_i] = self._format_cost_per_share(row[cost_i])

    def _apply_calc_to_row_widgets(self, row, calc):
        if calc.get("nav") is not None:
            self._write_cell(row[1], calc["nav"])
        if calc.get("shares") is not None:
            self._write_cell(row[3], calc["shares"])
        if calc.get("cost") is not None:
            self._write_cell(row[4], self._format_cost_per_share(calc["cost"]))
        fill_buy = self._maybe_fill_buy_shares(
            row[self.buy_col_index].get(), calc.get("buy_shares")
        )
        if fill_buy is not None:
            self._write_cell(row[self.buy_col_index], fill_buy)

    def _apply_calc_to_row_data(self, row_data, calc):
        expected = len(self.headers)
        while len(row_data) < expected:
            row_data.append("-")
        if calc.get("nav") is not None:
            row_data[1] = str(calc["nav"])
        if calc.get("shares") is not None:
            row_data[3] = str(calc["shares"])
        if calc.get("cost") is not None:
            row_data[4] = self._format_cost_per_share(calc["cost"])
        current_buy = row_data[self.buy_col_index] if len(row_data) > self.buy_col_index else "0"
        fill_buy = self._maybe_fill_buy_shares(current_buy, calc.get("buy_shares"))
        if fill_buy is not None:
            row_data[self.buy_col_index] = str(fill_buy)

    def update_result(self, row_index, save=True):
        row_index_in_list = row_index - DATA_GRID_OFFSET
        if row_index_in_list < 0 or row_index_in_list >= len(self.rows):
            return
        row = self.rows[row_index_in_list]

        try:
            date_str = self._date_str_from_cell(row[0])
            fund_code = self.sheet_name

            if len(fund_code) != 6 or not fund_code.isdigit():
                print("Invalid Fund Code given, plz check and re-try with a 6-digit code.\n")
                self._recompute_lot_state()
                return

            try:
                buy_money_amount = self._parse_buy_money(row[2].get())
            except ValueError:
                print("money amount for buying is not right, plz check and re-try.\n")
                return

            if buy_money_amount <= 0:
                calc = self._calc_subscription_fields_local_clear(buy_money_amount)
                self._apply_calc_to_row_widgets(row, calc)
                # 无申购仍尽量补净值，供至今涨幅（有缓存时几乎不耗时）
                nav = xalpha_tool.fetch_otc_fund_net_value(fund_code, date_str)
                if nav is not None:
                    self._write_cell(row[1], nav)
                self.update_profit_cell(row)
                self._recompute_lot_state()
                if save and not self._batch_loading:
                    self.save_data()
                return

            calc = self._calc_subscription_fields(fund_code, date_str, buy_money_amount)
            if calc["error"] and buy_money_amount > 0:
                print(f"错误: {calc['error']}！\n")
                # 金额>0 但失败时不改写到账/成本，避免把旧正确值清掉；若拿到了净值仍写入
                if calc["nav"] is not None:
                    self._write_cell(row[1], calc["nav"])
                self.update_profit_cell(row)
                return

            self._apply_calc_to_row_widgets(row, calc)
            self.update_profit_cell(row)
            self._recompute_lot_state()
            if save and not self._batch_loading:
                self.save_data()

        except Exception as e:
            print(f"Error calculating result for row {row_index}: {e}")

    def update_all_results(self, save=True):
        for i in range(DATA_GRID_OFFSET, len(self.rows) + DATA_GRID_OFFSET):
            self.update_result(i, save=False)
        if save and not self._batch_loading:
            self.save_data()

    def _set_refresh_button_busy(self, busy):
        btn = getattr(self, "update_profits_btn", None)
        if btn is None:
            return
        try:
            btn.configure(state=("disabled" if busy else "normal"))
            btn.configure(text=("刷新中…" if busy else "刷新利润/涨幅"))
        except Exception:
            pass

    def _collect_subscribe_jobs(self, only_missing=False):
        """收集需要联网重算的行。only_missing=True 时只收「能算却未填」的行。"""
        jobs = []
        fund_code = self.sheet_name
        if len(fund_code) != 6 or not fund_code.isdigit():
            return jobs

        for i, row in enumerate(self.rows):
            date_str = self._date_str_from_cell(row[0])
            try:
                buy_money = self._parse_buy_money(row[2].get())
            except ValueError:
                continue
            nav = row[1].get() if len(row) > 1 else ""
            shares = row[3].get() if len(row) > 3 else ""
            cost = row[4].get() if len(row) > 4 else ""
            if buy_money <= 0:
                calc = self._calc_subscription_fields_local_clear(buy_money)
                self._apply_calc_to_row_widgets(row, calc)
                if only_missing and date_str and self._is_blank_cell(nav):
                    jobs.append(
                        {
                            "kind": "visible",
                            "index": i,
                            "date": date_str,
                            "money": 0.0,
                        }
                    )
                continue
            if only_missing and not self._subscribe_fields_incomplete(
                nav, shares, cost, buy_money
            ):
                continue
            jobs.append(
                {
                    "kind": "visible",
                    "index": i,
                    "date": date_str,
                    "money": buy_money,
                }
            )

        for i, row in enumerate(self._hidden_rows_data):
            normalized = self._normalize_row(row)
            if normalized is None:
                continue
            row[:] = normalized
            date_str = str(row[0])[:10]
            try:
                buy_money = self._parse_buy_money(str(row[2]))
            except ValueError:
                continue
            nav = row[1] if len(row) > 1 else ""
            shares = row[3] if len(row) > 3 else ""
            cost = row[4] if len(row) > 4 else ""
            if buy_money <= 0:
                self._apply_calc_to_row_data(
                    row, self._calc_subscription_fields_local_clear(buy_money)
                )
                if only_missing and date_str and self._is_blank_cell(nav):
                    jobs.append(
                        {
                            "kind": "hidden",
                            "index": i,
                            "date": date_str,
                            "money": 0.0,
                        }
                    )
                continue
            if only_missing and not self._subscribe_fields_incomplete(
                nav, shares, cost, buy_money
            ):
                continue
            jobs.append(
                {
                    "kind": "hidden",
                    "index": i,
                    "date": date_str,
                    "money": buy_money,
                }
            )
        return jobs

    def _startup_fill_missing(self):
        """启动后补齐能算却未填入的净值/到账/成本（不覆盖已有值所在的完整行）。"""
        if not self.winfo_exists():
            return
        self.refresh_derived_metrics(only_missing=True)

    def _apply_subscribe_job_results(self, results):
        for job, calc in results:
            if calc is None:
                continue
            if calc.get("error") and job["money"] > 0:
                print(f"错误: {calc['error']}！\n")
                if calc.get("nav") is not None:
                    if job["kind"] == "visible" and 0 <= job["index"] < len(self.rows):
                        self._write_cell(self.rows[job["index"]][1], calc["nav"])
                    elif job["kind"] == "hidden" and 0 <= job["index"] < len(
                        self._hidden_rows_data
                    ):
                        self._hidden_rows_data[job["index"]][1] = str(calc["nav"])
                continue
            if job["kind"] == "visible" and 0 <= job["index"] < len(self.rows):
                self._apply_calc_to_row_widgets(self.rows[job["index"]], calc)
            elif job["kind"] == "hidden" and 0 <= job["index"] < len(
                self._hidden_rows_data
            ):
                self._apply_calc_to_row_data(self._hidden_rows_data[job["index"]], calc)

    def refresh_derived_metrics(self, only_missing=False):
        """利润/涨幅本地即刷；申购重算后台一次拉齐（同基金只请求一次）。
        only_missing=True：只补净值/到账/成本仍为空的行（启动用）；按钮刷新传 False 重算全部有申购的行。
        """
        if self._refresh_busy:
            return

        # 成本 3 位小数：本地立刻改显示，不依赖后台重算
        self._reformat_all_cost_displays()

        jobs = self._collect_subscribe_jobs(only_missing=only_missing)
        # 先本地刷一遍（含金额=0 清空后的利润/满7日）
        self.update_profits()
        self._recompute_lot_state()
        if not jobs:
            # 启动补缺若无需联网，不强制写盘
            if not self._batch_loading and not only_missing:
                self.save_data()
            return

        self._refresh_busy = True
        self._refresh_gen += 1
        gen = self._refresh_gen
        if not only_missing:
            self._set_refresh_button_busy(True)
        fund_code = self.sheet_name

        def worker():
            results = []
            try:
                # 预热一次 fundinfo；后续 subscribe/净值全走缓存
                xalpha_tool.get_fund(fund_code)
                for job in jobs:
                    calc = self._calc_subscription_fields(
                        fund_code, job["date"], job["money"]
                    )
                    results.append((job, calc))
            except Exception as e:
                print(f"刷新申购数据失败: {e}")
                results = []

            def finish():
                if gen != self._refresh_gen:
                    return
                try:
                    self._apply_subscribe_job_results(results)
                    self.update_profits()
                    self._recompute_lot_state()
                    if not self._batch_loading:
                        self.save_data()
                finally:
                    self._refresh_busy = False
                    if not only_missing:
                        self._set_refresh_button_busy(False)

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _set_readonly_entry(self, entry, value):
        entry.configure(state="normal")
        entry.delete(0, tk.END)
        entry.insert(0, value)
        entry.configure(state="readonly")

    def update_clock(self):
        try:
            now_text = datetime.now().strftime("%Y-%m-%d %H:%M")
            self._set_readonly_entry(self.top_time_slot, now_text)
            # 满7日相对「今天」；跨日时顺带刷新
            self._recompute_lot_state()
        except Exception as e:
            print("Clock update failed:", e)
        finally:
            # Minute precision is enough; refresh about once per minute
            self._clock_timer_id = self.after(60_000, self.update_clock)

    def update_price(self):
        try:
            self._set_market_price_display("-")

            if self.sheet_name.startswith("50"):
                fund_code = "SH" + self.sheet_name
            elif self.sheet_name.startswith("16"):
                fund_code = "SZ" + self.sheet_name
            else:
                print("Invalid LOF code given, plz check and re-try.\n")
                return

            latest_info = xalpha_tool.fetch_realtime_stock_info(fund_code)
            if latest_info is not None:
                percentage = latest_info["percent"]
                latest_price = latest_info["current"]
                self._set_market_price_display(latest_price)

                self.top_slot5.delete(0, tk.END)
                self.top_slot5.insert(0, f"{percentage}%")
                if percentage > 0:
                    self.top_slot5.configure(fg="red")
                else:
                    self.top_slot5.configure(fg="green")

                self.update_profits()

        except Exception as e:
            print("Price update failed:", e)
        finally:
            self._timer_id = self.after(5000, self.update_price)

    def destroy(self):
        self._unbind_mousewheel(None)
        for attr in ("_timer_id", "_clock_timer_id", "_fit_after_id"):
            timer_id = getattr(self, attr, None)
            if timer_id is not None:
                try:
                    self.after_cancel(timer_id)
                except Exception:
                    pass
        super().destroy()

    def safe_float(self, value_or_widget):
        try:
            if hasattr(value_or_widget, "get"):
                raw = value_or_widget.get()
            else:
                raw = value_or_widget
            return float(raw.strip()) if raw.strip() else 0.0
        except (ValueError, TypeError):
            return 0.0

    def update_profits(self):
        for row in self.rows:
            self.update_profit_cell(row)

    def update_profit_cell(self, row):
        if not isinstance(row[0], DateEntry):
            return
        profit_cell = row[self.profit_col_index]
        gain_cell = row[self.gain_col_index]
        if not isinstance(profit_cell, tk.Entry):
            return

        cost = self.safe_float(row[4].get())
        nav = self.safe_float(row[1].get())

        if cost > 0 and self.rt_price > 0:
            profit_per_share = self.rt_price - cost
            num_shares_bought = self.safe_float(row[3].get())
            # 华宝 LOF 卖出保底 0.2 手续费
            profit = round(profit_per_share * num_shares_bought - 0.2, 2)

            profit_cell.delete(0, tk.END)
            profit_cell.insert(0, str(profit))
            profit_cell.configure(fg="red" if profit > 0 else "green")
        else:
            profit_cell.delete(0, tk.END)
            profit_cell.insert(0, "-")
            profit_cell.configure(fg="blue")

        if isinstance(gain_cell, tk.Entry):
            # 至今涨幅 = (当前场内价 - 当日净值) / 当日净值
            if nav > 0 and self.rt_price > 0:
                gain_pct = round((self.rt_price - nav) / nav * 100, 2)
                gain_cell.delete(0, tk.END)
                gain_cell.insert(0, f"{gain_pct}%")
                gain_cell.configure(fg="red" if gain_pct > 0 else "green")
            else:
                gain_cell.delete(0, tk.END)
                gain_cell.insert(0, "-")
                gain_cell.configure(fg="blue")

    @staticmethod
    def add_trading_days(start_date, n):
        """Move forward n trading days (Mon–Fri only; holidays ignored)."""
        if n <= 0:
            return start_date
        d = start_date
        added = 0
        while added < n:
            d += timedelta(days=1)
            if d.weekday() < 5:
                added += 1
        return d

    def _raw_buckets_for_tiers(self, lots, today, tiers):
        """按相对今天的持有天数，把剩余批次份额分到给定赎回档（原始 float）。"""
        buckets = [0.0] * len(tiers)
        for lot in lots:
            remain = lot.get("remain", 0) or 0
            if remain <= 0:
                continue
            # 申购批次确认日未到之前不计入赎回持有天数
            days = (today - lot["confirm"]).days
            if days < 0:
                continue
            for ti, tier in enumerate(tiers):
                lo = int(tier.get("day_lo") or 0)
                hi = tier.get("day_hi")
                if days < lo:
                    continue
                if hi is not None and days >= int(hi):
                    continue
                buckets[ti] += remain
                break
        return buckets

    def _bucket_remaining_by_tier(self, lots, today, tiers=None):
        tiers = tiers if tiers is not None else self.redeem_tiers
        return [
            self._format_share_amount(v)
            for v in self._raw_buckets_for_tiers(lots, today, tiers)
        ]

    def _filter_nonzero_tiers(self, lots, today):
        """当前持仓中份额>0 的赎回档；全为 0 则返回空列表（不显示赎回列）。"""
        all_tiers = self._all_redeem_tiers or self.redeem_tiers
        if not all_tiers:
            return []
        raw = self._raw_buckets_for_tiers(lots, today, all_tiers)
        return [dict(t) for t, v in zip(all_tiers, raw) if v > 1e-9]

    def _share_flows_for_row(self, row, as_of, confirm_n):
        """
        四个动作对持仓的生效日不同：
        - 到账份额（申购）：T+confirm_n 才增加，持有期从确认日起算
        - 买入份额：T 日增加；同日已有到账时只计超出部分，避免预填重复
        - 卖出份额：T 日从已确认批次扣减
        - 赎回份额：T+confirm_n 才扣减；确认前仍留在分段里
        返回 (生效日, phase, 份额, 来源)。同一行、同一生效日先流出再流入。
        """
        credited = self.safe_float(row[self.credited_col_index]) if len(row) > self.credited_col_index else 0.0
        bought = self.safe_float(row[self.buy_col_index]) if len(row) > self.buy_col_index else 0.0
        sold = self.safe_float(row[self.sell_col_index]) if len(row) > self.sell_col_index else 0.0
        redeemed = (
            self.safe_float(row[self.redeem_shares_col_index])
            if len(row) > self.redeem_shares_col_index
            else 0.0
        )

        buy_lot = max(0.0, bought - credited) if credited > 0 else bought
        confirm = self.add_trading_days(as_of, confirm_n)
        flows = []
        if sold > 0:
            flows.append((as_of, _PHASE_OUT, sold, "sell"))
        if redeemed > 0:
            flows.append((confirm, _PHASE_OUT, redeemed, "redeem"))
        if buy_lot > 0:
            flows.append((as_of, _PHASE_IN, buy_lot, "buy"))
        if credited > 0:
            flows.append((confirm, _PHASE_IN, credited, "subscribe"))
        return flows

    def _lots_from_flows(self, flows, today):
        """按 (生效日, 行序, 先出后进) 重放；生效日晚于今天的动作先不入账。"""
        ordered = sorted(flows, key=lambda item: (item[0], item[1], item[2]))
        lots = []
        for effective, _seq, phase, shares, source in ordered:
            if effective > today:
                continue
            if phase == _PHASE_IN:
                lots.append({"confirm": effective, "remain": shares, "source": source})
                continue
            left = shares
            for lot in lots:
                if left <= 0:
                    break
                if lot["confirm"] <= effective and lot["remain"] > 0:
                    take = min(lot["remain"], left)
                    lot["remain"] -= take
                    left -= take
        return lots

    def _recompute_lot_state(self):
        """FIFO：卖出 T 日扣、赎回 T+x 确认后扣；赎回分段相对今天分档；零份额档不展示。"""
        self._sync_full_from_ui()
        if not self._full_rows_data:
            return

        today = datetime.now().date()
        confirm_n = 2 if self.is_qdii_var.get() else 1
        redeem_cols = list(self.redeem_col_indices)
        n_redeem = len(redeem_cols)
        blank = ["-"] * n_redeem

        indexed = list(enumerate(self._full_rows_data))
        indexed.sort(
            key=lambda item: (
                self._parse_row_date(item[1]) or datetime.min.date(),
                item[0],
            )
        )

        parsed = []
        for orig_i, row in indexed:
            normalized = self._normalize_row(row)
            if normalized is None:
                parsed.append({"orig_i": orig_i, "row": row, "as_of": None, "flows": []})
                continue
            row[:] = normalized
            as_of = self._parse_row_date(row)
            flows = self._share_flows_for_row(row, as_of, confirm_n) if as_of else []
            parsed.append({"orig_i": orig_i, "row": row, "as_of": as_of, "flows": flows})

        tagged = []
        for seq, item in enumerate(parsed):
            for effective, phase, shares, source in item["flows"]:
                tagged.append((effective, seq, phase, shares, source))

        results = {}
        final_lots = self._lots_from_flows(tagged, today)
        for seq, item in enumerate(parsed):
            orig_i = item["orig_i"]
            row = item["row"]
            if item["as_of"] is None:
                results[orig_i] = list(blank)
                for ci in redeem_cols:
                    if ci < len(row):
                        row[ci] = "-"
                continue
            lots = self._lots_from_flows([ev for ev in tagged if ev[1] <= seq], today)
            bucket_vals = self._bucket_remaining_by_tier(lots, today, self.redeem_tiers)
            results[orig_i] = bucket_vals
            for j, ci in enumerate(redeem_cols):
                if ci < len(row):
                    row[ci] = bucket_vals[j] if j < len(bucket_vals) else "-"

        n_visible = len(self.rows)
        for i, row_widgets in enumerate(self.rows):
            if i >= len(self._full_rows_data):
                break
            vals = results.get(i)
            if vals is None:
                vals = [
                    self._full_rows_data[i][ci] if ci < len(self._full_rows_data[i]) else "-"
                    for ci in redeem_cols
                ]
            for j, ci in enumerate(redeem_cols):
                if ci < len(row_widgets):
                    self._set_entry_value(
                        row_widgets[ci],
                        vals[j] if j < len(vals) else "-",
                        readonly=True,
                    )

        if self._history_collapsed and self._hidden_rows_data is not None:
            self._hidden_rows_data = [
                list(r) for r in self._full_rows_data[n_visible:]
            ]

        # 当前持仓为 0 的赎回档隐藏（避免空列占位）
        if not self._filter_lock:
            visible_tiers = self._filter_nonzero_tiers(final_lots, today)
            old_labels = [t["label"] for t in self.redeem_tiers]
            new_labels = [t["label"] for t in visible_tiers]
            if old_labels != new_labels:
                self._filter_lock = True
                try:
                    self.apply_redeem_tiers(visible_tiers)
                finally:
                    self._filter_lock = False

    def save_data(self):
        self._sync_full_from_ui()
        data = {
            "top_info": {
                "fund_name": self.top_slot2.get(),
                "real_time_price": self._format_3dp(self.top_slot4.get()),
                "is_qdii": bool(self.is_qdii_var.get()),
                "has_redeem_shares_col": True,
            },
            "rows": self._full_rows_data,
        }

        with open(self.save_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @staticmethod
    def latest_trading_day(from_date=None):
        """最新交易日：当天若为工作日则用之，否则回退到上一周五（不含法定节假日）。"""
        d = from_date or datetime.now().date()
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    @staticmethod
    def upcoming_trading_day(from_date=None):
        """即将到来的交易日：当天若为工作日则用之，否则前进到下周一（不含法定节假日）。"""
        d = from_date or datetime.now().date()
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d

    def _max_existing_row_date(self):
        """已有行（含折叠隐藏）中的最晚日期；没有则 None。"""
        self._sync_full_from_ui()
        best = None
        for row in self._full_rows_data:
            d = self._parse_row_date(row)
            if d is not None and (best is None or d > best):
                best = d
        return best

    def default_date_for_top_insert(self):
        """
        顶部插入默认日期：已有条目最近日期之后的「当前最新」交易日。
        - 先取即将到来的交易日（周末则进到下周一）；
        - 若不晚于已有最近日期，则改为该日期之后的第 1 个交易日。
        """
        upcoming = self.upcoming_trading_day()
        max_existing = self._max_existing_row_date()
        if max_existing is None:
            return upcoming
        if upcoming > max_existing:
            return upcoming
        return self.add_trading_days(max_existing, 1)

    @staticmethod
    def _parse_row_date(row_data):
        try:
            return datetime.strptime(str(row_data[0])[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    def _header_height_px(self):
        for child in self.table_frame.grid_slaves(row=0):
            h = child.winfo_height()
            if h > 1:
                return h
            req = child.winfo_reqheight()
            if req > 1:
                return req
        return 28

    def _data_row_height_px(self):
        if self.rows:
            cell = self.rows[0][0]
            h = cell.winfo_height()
            if h > 1:
                return h
            req = cell.winfo_reqheight()
            if req > 1:
                return req
        return 26

    def _max_visible_rows(self):
        """当前 canvas 高度内能完整放下的数据行数（不含表头）。"""
        self.update_idletasks()
        canvas_h = int(self.canvas.winfo_height())
        if canvas_h <= 1:
            return FALLBACK_VISIBLE_ROWS
        row_h = max(self._data_row_height_px(), 20)
        header_h = max(self._header_height_px(), 20)
        avail = canvas_h - header_h
        if avail < row_h:
            return 1
        return max(1, avail // row_h)

    def _extract_one_row_data(self, row_widgets):
        row_data = []
        cost_i = 4
        for col_i, cell in enumerate(row_widgets):
            if isinstance(cell, DateEntry):
                val = cell.get_date().strftime("%Y-%m-%d")
            elif isinstance(cell, (tk.Entry, ttk.Combobox)):
                val = cell.get()
            elif isinstance(cell, tk.Label):
                val = cell["text"]
            else:
                val = ""
            if col_i == cost_i:
                val = self._format_cost_per_share(val)
            row_data.append(val)
        return row_data

    def _fold_overflow_into_hidden(self):
        """折叠态下：超出可视容量的底部行收入「更多交易」。"""
        if not self._history_collapsed or self._batch_loading or self._refitting:
            return
        cap = self._max_visible_rows()
        folded = False
        while len(self.rows) > cap:
            row_widgets = self.rows.pop()
            row_data = self._extract_one_row_data(row_widgets)
            for widget in row_widgets:
                widget.destroy()
            self._hidden_rows_data.insert(0, row_data)
            folded = True
        if folded:
            self.selected_row_indices = {
                i for i in self.selected_row_indices if i < len(self.rows)
            }
            if self._anchor_row_index is not None and self._anchor_row_index >= len(self.rows):
                self._anchor_row_index = None
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _refit_history_to_viewport(self):
        """按当前窗口高度重算可见/隐藏行（仅折叠态）。"""
        self._fit_after_id = None
        if self._batch_loading or not self._history_collapsed or self._refitting:
            return
        if not self.winfo_exists():
            return

        self._refitting = True
        try:
            self._sync_full_from_ui()
            full = [list(r) for r in self._full_rows_data]
            if not full:
                return

            cap = self._max_visible_rows()
            visible = full[:cap]
            hidden = full[cap:]

            if len(visible) == len(self.rows) and len(hidden) == len(self._hidden_rows_data):
                return

            self._batch_loading = True
            try:
                self._clear_data_rows()
                self._hidden_rows_data = [list(r) for r in hidden]
                # 保持折叠策略，便于之后溢出自动收入「更多」
                self._history_collapsed = True
                for row in visible:
                    prefill = dict(zip(self.headers, row))
                    self.add_row_on_bottom(prefill=prefill)
                self._full_rows_data = visible + self._hidden_rows_data
            finally:
                self._batch_loading = False

            self.render_action_buttons()
            self.update_profits()
            self._recompute_lot_state()
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            self.canvas.yview_moveto(0.0)
        finally:
            self._refitting = False

    def _normalize_row(self, row):
        """Pad/migrate legacy rows to BASE + 当前赎回分档列数。"""
        if not isinstance(row, list):
            return None
        expected = len(self.headers)
        out = list(row)

        def _buy_from_credited(cols):
            if len(cols) > 3 and str(cols[3]).strip() not in ("", "-"):
                return str(cols[3]).strip()
            return "0"

        # 旧 8 列：…涨幅, 卖出 → 插入买入
        if len(out) == 8:
            out = out[:7] + [_buy_from_credited(out)] + out[7:]
        # 旧 9 列（无买入 + 满7日）：插买入并丢掉满7日。仅在读入「赎回份额」列之前的文件时使用
        elif len(out) == 9 and self._legacy_without_redeem_col and expected != 9:
            out = out[:7] + [_buy_from_credited(out)] + [out[7]]

        if self._legacy_without_redeem_col:
            # 前 9 列到卖出份额；其后是旧分段展示值。插入赎回份额 0 后丢掉旧尾列
            if len(out) >= PRE_REDEEM_COL_BASE:
                out = out[:PRE_REDEEM_COL_BASE] + ["0"]
        elif len(out) > BASE_COL_COUNT:
            out = out[:BASE_COL_COUNT]

        while len(out) < BASE_COL_COUNT:
            if len(out) == 6:
                out.append("-")
            elif len(out) == 7:
                out.append(_buy_from_credited(out))
            elif len(out) in (8, 9):
                out.append("0")
            else:
                out.append("-")

        while len(out) < expected:
            out.append("-")
        if len(out) > expected:
            out = out[:expected]

        if len(out) > 4:
            out[4] = self._format_cost_per_share(out[4])
        return out

    def apply_redeem_tiers(self, tiers):
        """按赎回分段重建表头与尾列（不丢买入/卖出等基础数据）。tiers 可为空（隐藏全部赎回列）。"""
        if tiers is None:
            tiers = [dict(t) for t in xalpha_tool.DEFAULT_REDEEM_TIERS]
        else:
            tiers = [dict(t) for t in tiers]

        new_headers = self._compose_headers(tiers)
        if new_headers == list(self.headers):
            self.redeem_tiers = tiers
            return False

        was_batch = self._batch_loading
        self._batch_loading = True
        try:
            self._sync_full_from_ui()
            n_visible = len(self.rows)
            collapsed = bool(self._history_collapsed)

            def _trim(rows):
                trimmed = []
                for r in rows:
                    base = list(r)[:BASE_COL_COUNT]
                    while len(base) < BASE_COL_COUNT:
                        base.append("-")
                    base.extend(["-"] * len(tiers))
                    trimmed.append(base)
                return trimmed

            full = _trim(self._full_rows_data)

            self.redeem_tiers = tiers
            self.headers = new_headers
            self._refresh_col_indices()

            self._clear_data_rows()
            self.draw_table()

            if collapsed:
                visible_data = full[:n_visible]
                self._hidden_rows_data = full[n_visible:]
                self._history_collapsed = True
                self._full_rows_data = visible_data + self._hidden_rows_data
            else:
                visible_data = full
                self._hidden_rows_data = []
                self._history_collapsed = False
                self._full_rows_data = full

            for row in visible_data:
                prefill = dict(zip(self.headers, row))
                self.add_row_on_bottom(prefill=prefill)
        finally:
            self._batch_loading = was_batch

        self.render_action_buttons()
        self._recompute_lot_state()
        if not self._batch_loading:
            self.save_data()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._notify_window_fit()
        return True

    def reload_redeem_tiers_async(self):
        """后台拉取 feeinfo / 申购费率并换列（不卡 UI）。"""
        code = str(self.sheet_name)
        if not (code.isdigit() and len(code) == 6):
            return
        if self._tiers_loading:
            return
        self._tiers_loading = True

        def worker():
            tiers = None
            rate = None
            try:
                tiers = xalpha_tool.get_redeem_tiers(code)
                rate = xalpha_tool.get_subscribe_actual_rate(code)
            except Exception as e:
                print(f"拉取费率信息失败: {e}")

            def finish():
                self._tiers_loading = False
                if not self.winfo_exists():
                    return
                if rate is not None:
                    self._subscribe_actual_rate = rate
                if tiers:
                    self._all_redeem_tiers = [dict(t) for t in tiers]
                    # 先套全部分档，_recompute_lot_state 会去掉当前为 0 的档
                    self.apply_redeem_tiers(tiers)
                elif self._subscribe_actual_rate is not None:
                    # 仅刷新申购金额标题上的费率
                    self.apply_redeem_tiers(self.redeem_tiers)

            try:
                self.after(0, finish)
            except Exception:
                self._tiers_loading = False

        threading.Thread(target=worker, daemon=True).start()

    def _extract_visible_rows_data(self):
        rows_data = []
        cost_i = 4
        for row in self.rows:
            row_data = []
            for col_i, cell in enumerate(row):
                if isinstance(cell, DateEntry):
                    val = cell.get_date().strftime("%Y-%m-%d")
                elif isinstance(cell, (tk.Entry, ttk.Combobox)):
                    val = cell.get()
                elif isinstance(cell, tk.Label):
                    val = cell["text"]
                else:
                    val = ""
                if col_i == cost_i:
                    val = self._format_cost_per_share(val)
                row_data.append(val)
            rows_data.append(row_data)
        return rows_data

    def _sync_full_from_ui(self):
        visible = self._extract_visible_rows_data()
        if self._history_collapsed and self._hidden_rows_data:
            self._full_rows_data = visible + self._hidden_rows_data
        else:
            self._full_rows_data = visible

    def _split_visible_and_hidden(self, rows_data, max_visible=None):
        """按界面容量切分：表序靠前的留下，放不下的收入「更多交易」。"""
        if not rows_data:
            return [], []
        if max_visible is None:
            max_visible = self._max_visible_rows()
        max_visible = max(1, int(max_visible))
        if len(rows_data) <= max_visible:
            return [list(r) for r in rows_data], []
        visible = [list(r) for r in rows_data[:max_visible]]
        hidden = [list(r) for r in rows_data[max_visible:]]
        return visible, hidden

    def _clear_data_rows(self):
        for row in self.rows:
            for widget in row:
                widget.destroy()
        self.rows.clear()
        self.selected_row_indices.clear()
        self._anchor_row_index = None

    def show_more_history(self):
        if not self._hidden_rows_data:
            return

        self._sync_full_from_ui()
        hidden = list(self._hidden_rows_data)
        self._hidden_rows_data = []
        self._history_collapsed = False

        self._batch_loading = True
        try:
            for row in hidden:
                normalized = self._normalize_row(row)
                if normalized is None:
                    continue
                prefill = dict(zip(self.headers, normalized))
                self.add_row_on_bottom(prefill=prefill)
        finally:
            self._batch_loading = False

        self.render_action_buttons()
        self.update_profits()
        self._recompute_lot_state()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.yview_moveto(1.0)

    def load_data(self):
        self._batch_loading = True
        self._full_rows_data = []
        self._hidden_rows_data = []
        self._history_collapsed = True
        try:
            if not os.path.exists(self.save_path):
                self.top_slot2.insert(0, "")
                self.top_slot4.insert(0, "")
                self.top_slot5.insert(0, "")
                for _ in range(3):
                    self.add_row_on_bottom()
                self._full_rows_data = self._extract_visible_rows_data()
                return

            try:
                with open(self.save_path, "r", encoding="utf-8") as f:
                    saved_data = json.load(f)

                if isinstance(saved_data, list):
                    top_info = {"fund_name": "", "real_time_price": ""}
                    rows_data = saved_data
                else:
                    top_info = saved_data.get("top_info", {})
                    rows_data = saved_data.get("rows", [])

                self._legacy_without_redeem_col = not bool(
                    top_info.get("has_redeem_shares_col")
                )
                valid_rows = []
                try:
                    for row in rows_data:
                        normalized = self._normalize_row(row)
                        if normalized is not None:
                            valid_rows.append(normalized)
                finally:
                    self._legacy_without_redeem_col = False
                self._full_rows_data = valid_rows

                fund_name = top_info.get("fund_name", "") or ""
                self.top_slot2.delete(0, tk.END)
                self.top_slot2.insert(0, fund_name)

                self._set_market_price_display(top_info.get("real_time_price", "") or "-")

                self.top_slot5.delete(0, tk.END)

                self.is_qdii_var.set(bool(top_info.get("is_qdii", False)))

                # 先用回退容量切分；布局完成后 _refit_history_to_viewport 再按真实高度调整
                visible, hidden = self._split_visible_and_hidden(
                    self._full_rows_data, max_visible=FALLBACK_VISIBLE_ROWS
                )
                self._hidden_rows_data = hidden
                self._history_collapsed = True
                # Keep a stable save order while collapsed: visible then hidden
                self._full_rows_data = [list(r) for r in visible] + [list(r) for r in hidden]

                for row in visible:
                    prefill = dict(zip(self.headers, row))
                    self.add_row_on_bottom(prefill=prefill)

                # Resolve fund name later if missing (avoid blocking startup)
                if not fund_name and self.sheet_name.isdigit():
                    self.after(0, self._fetch_fund_name_async)

            except Exception as e:
                print(f"加载失败: {e}")
                self.top_slot2.delete(0, tk.END)
                self.top_slot4.delete(0, tk.END)
                self.top_slot5.delete(0, tk.END)
                for _ in range(3):
                    self.add_row_on_bottom()
                self._full_rows_data = self._extract_visible_rows_data()
                self._hidden_rows_data = []
                self._history_collapsed = False
        finally:
            self._batch_loading = False
            self._reformat_all_cost_displays()
            self.render_action_buttons()
            self._recompute_lot_state()
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            self.after(80, self._refit_history_to_viewport)

    def _fetch_fund_name_async(self):
        try:
            fund_name = xalpha_tool.get_fund_name(self.sheet_name)
            if fund_name and not self.top_slot2.get().strip():
                self.top_slot2.delete(0, tk.END)
                self.top_slot2.insert(0, fund_name)
        except Exception as e:
            print(f"获取基金名称失败: {e}")

    def highlight_selected_row(self, widget):
        # Button-1 已处理过多选时，避免 FocusIn 覆盖选择集
        if self._selection_from_click:
            self._selection_from_click = False
            self._refresh_selection_highlight()
            return

        row_index = self._row_index_of_widget(widget)
        if row_index is None:
            return
        self.selected_row_indices = {row_index}
        self._anchor_row_index = row_index
        self._refresh_selection_highlight()

    def _refresh_selection_highlight(self):
        for row_index, row in enumerate(self.rows):
            selected = row_index in self.selected_row_indices
            bg = "#e0e0ff" if selected else "white"
            for cell in row:
                if isinstance(cell, tk.Entry) and not isinstance(cell, DateEntry):
                    try:
                        cell.configure(bg=bg)
                    except Exception:
                        pass
