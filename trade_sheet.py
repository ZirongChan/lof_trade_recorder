import os
import json
import time

import tkinter as tk
from tkinter import ttk
from tkcalendar import DateEntry

from datetime import datetime, timedelta
import calendar

import xalpha_tool

DEBUG_TIMING = False

# Grid layout: row 0 = headers, row 1+ = data (top info is outside the table)
DATA_GRID_OFFSET = 1
HISTORY_MONTHS = 3
FALLBACK_VISIBLE_ROWS = 3


class TradeSheet(tk.Frame):
    def __init__(self, parent, headers, sheet_name):
        if DEBUG_TIMING:
            self._init_start = time.perf_counter()

        super().__init__(parent)

        self.headers = headers
        self.sheet_name = sheet_name
        self.rows = []
        self.cell_width = 14
        self.rt_price = 0
        self.profit_col_index = self.headers.index("预估利润")
        self.gain_col_index = self.headers.index("至今涨幅")
        self.sell_col_index = self.headers.index("卖出份额")
        self.mature_col_index = self.headers.index("满7日剩余")
        self.selected_row_indices = set()
        self._anchor_row_index = None
        self._selection_from_click = False
        self._batch_loading = False
        self._full_rows_data = []
        self._hidden_rows_data = []
        self._history_collapsed = True
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

    def _on_frame_configure(self, event):
        # Only update scroll region — do not grow canvas to full table height
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

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

    def draw_table(self):
        for i in range(len(self.headers)):
            self.table_frame.columnconfigure(i, weight=1, uniform="colgroup")
        for i, header in enumerate(self.headers):
            lbl = tk.Label(
                self.table_frame,
                text=header,
                borderwidth=1,
                relief="solid",
                width=self.cell_width,
                anchor="center",
            )
            lbl.grid(row=0, column=i, sticky="nsew")

    def draw_top_info_row(self):
        # 基金名称 | 名称 | 当前时间 | 当前场内价格 | 价格 | 涨跌幅 | QDII
        for c in range(7):
            self.top_info_bar.columnconfigure(c, weight=1, uniform="topinfo")

        self.top_slot1 = tk.Label(
            self.top_info_bar, text="基金名称", width=self.cell_width, anchor="center"
        )
        self.top_slot1.grid(row=0, column=0, sticky="nsew", padx=(0, 2))

        self.top_slot2 = tk.Entry(
            self.top_info_bar, width=self.cell_width * 2, justify="center", fg="blue"
        )
        self.top_slot2.grid(row=0, column=1, sticky="nsew", padx=2)

        self.top_time_slot = tk.Entry(
            self.top_info_bar,
            width=self.cell_width + 4,
            justify="center",
            fg="#333333",
            state="readonly",
            readonlybackground=self.top_info_bar.cget("bg"),
        )
        self.top_time_slot.grid(row=0, column=2, sticky="nsew", padx=2)

        self.top_slot3 = tk.Label(
            self.top_info_bar, text="当前场内价格", width=self.cell_width, anchor="center"
        )
        self.top_slot3.grid(row=0, column=3, sticky="nsew", padx=2)

        self.top_slot4 = tk.Entry(
            self.top_info_bar, width=self.cell_width, justify="center", fg="blue"
        )
        self.top_slot4.grid(row=0, column=4, sticky="nsew", padx=2)

        self.top_slot5 = tk.Entry(
            self.top_info_bar, width=self.cell_width, justify="center"
        )
        self.top_slot5.grid(row=0, column=5, sticky="nsew", padx=2)

        self.qdii_check = ttk.Checkbutton(
            self.top_info_bar,
            text="QDII（T+2确认）",
            variable=self.is_qdii_var,
            command=self._on_qdii_toggle,
        )
        self.qdii_check.grid(row=0, column=6, sticky="nsew", padx=(2, 0))

    def _on_qdii_toggle(self):
        self._recompute_lot_state()
        if not self._batch_loading:
            self.save_data()

    def _is_input_header(self, header):
        return header in ("Date", "日期", "Sub. in Currency", "申购金额", "卖出份额")

    def _bind_row_cell(self, cell, header):
        cell.bind("<Button-1>", self._on_cell_button1, add="+")
        cell.bind("<FocusIn>", lambda e, w=cell: self.highlight_selected_row(w))
        if header == "卖出份额":
            cell.bind("<Return>", self.handle_sell_return)
            cell.bind("<FocusOut>", self.handle_sell_return)
        elif self._is_input_header(header):
            cell.bind("<Return>", self.handle_entry_return)
            cell.bind("<FocusOut>", self.handle_entry_return)

    def _make_row_widgets(self, grid_row, prefill=None):
        row_widgets = []
        for col_index, header in enumerate(self.headers):
            if header in ("Date", "日期"):
                cell = DateEntry(
                    self.table_frame,
                    width=self.cell_width,
                    date_pattern="yyyy-mm-dd",
                    justify="center",
                )
                if prefill and header in prefill:
                    try:
                        cell.set_date(prefill[header])
                    except Exception:
                        pass
            elif header in ("Sub. in Currency", "申购金额"):
                cell = tk.Entry(self.table_frame, width=self.cell_width, justify="center")
                if prefill and header in prefill:
                    cell.insert(0, prefill[header])
                else:
                    cell.insert(0, "0")
            elif header == "卖出份额":
                cell = tk.Entry(self.table_frame, width=self.cell_width, justify="center")
                if prefill and header in prefill and str(prefill[header]).strip() not in ("", "-"):
                    cell.insert(0, prefill[header])
                else:
                    cell.insert(0, "0")
            elif header == "满7日剩余":
                cell = tk.Entry(
                    self.table_frame,
                    width=self.cell_width,
                    justify="center",
                    state="readonly",
                    readonlybackground="white",
                )
                raw = (prefill or {}).get(header, "-")
                self._set_entry_value(cell, raw if raw not in (None, "") else "-")
            else:
                cell = tk.Entry(self.table_frame, width=self.cell_width, justify="center")
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
        if prefill is None and self.rows:
            last_row = self.rows[-1]
            prefill = {}
            try:
                prev_date = last_row[0].get_date()
                prefill["日期"] = (prev_date + timedelta(days=-1)).strftime("%Y-%m-%d")
            except Exception as e:
                print("Could not parse previous date:", e)

        grid_row = len(self.rows) + DATA_GRID_OFFSET
        row_widgets = self._make_row_widgets(grid_row, prefill=prefill)
        self.rows.append(row_widgets)

        if not self._batch_loading:
            self.render_action_buttons()
            self._recompute_lot_state()
            self.save_data()
            self.canvas.yview_moveto(1.0)

    def add_row_on_top(self, prefill=None):
        if prefill is None and self.rows:
            first_row = self.rows[0]
            prefill = {}
            try:
                prev_date = first_row[0].get_date()
                prefill["日期"] = (prev_date + timedelta(days=1)).strftime("%Y-%m-%d")
            except Exception as e:
                print("Could not parse previous date:", e)

        # Shift existing data rows down by one grid index
        for i, existing_row in enumerate(self.rows):
            for widget in existing_row:
                widget.grid_configure(row=i + DATA_GRID_OFFSET + 1)

        row_widgets = self._make_row_widgets(DATA_GRID_OFFSET, prefill=prefill)
        self.rows.insert(0, row_widgets)

        if not self._batch_loading:
            self.render_action_buttons()
            self._recompute_lot_state()
            self.save_data()
            self.canvas.yview_moveto(0.0)

    def handle_entry_return(self, event):
        widget = event.widget
        try:
            grid_row = int(widget.grid_info()["row"])
        except (KeyError, TypeError, ValueError):
            return
        self.update_result(grid_row)

    def handle_sell_return(self, event):
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

    def update_result(self, row_index):
        row_index_in_list = row_index - DATA_GRID_OFFSET
        if row_index_in_list < 0 or row_index_in_list >= len(self.rows):
            return
        row = self.rows[row_index_in_list]

        try:
            date_str = row[0].get()
            fund_code = self.sheet_name

            if len(fund_code) != 6 or not fund_code.isdigit():
                print("Invalid Fund Code given, plz check and re-try with a 6-digit code.\n")
                self._recompute_lot_state()
                return

            buy_money_raw = row[2].get().strip()
            try:
                buy_money_amount = float(buy_money_raw) if buy_money_raw not in ("", "-") else 0.0
            except ValueError:
                print("money amount for buying is not right, plz check and re-try.\n")
                return

            # Sell-only day: no subscription network call
            if buy_money_amount <= 0:
                self._recompute_lot_state()
                if not self._batch_loading:
                    self.save_data()
                return

            net_value = xalpha_tool.fetch_otc_fund_net_value(fund_code, date_str)
            if net_value is None:
                print(f"错误: {date_str} 无净值数据 for {fund_code}！\n")
                return

            buy_res = xalpha_tool.subscribe(fund_code, buy_money_amount, date_str)
            if buy_res is None:
                print(f"Error: subscribe failed for row {row_index}")
                return

            # Only rewrite computed cells after both fetches succeed
            row[1].delete(0, tk.END)
            row[1].insert(0, net_value)
            row[3].delete(0, tk.END)
            row[3].insert(0, buy_res["share_amount_bought"])
            row[4].delete(0, tk.END)
            row[4].insert(0, buy_res["cost_per_share"])

            self.update_profit_cell(row)
            self._recompute_lot_state()
            if not self._batch_loading:
                self.save_data()

        except Exception as e:
            print(f"Error calculating result for row {row_index}: {e}")

    def update_all_results(self):
        for i in range(DATA_GRID_OFFSET, len(self.rows) + DATA_GRID_OFFSET):
            self.update_result(i)

    def refresh_derived_metrics(self):
        self.update_profits()
        self._recompute_lot_state()
        if not self._batch_loading:
            self.save_data()

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
            self.top_slot4.delete(0, tk.END)
            self.top_slot4.insert(0, "-")

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
                self.rt_price = latest_price

                self.top_slot4.delete(0, tk.END)
                self.top_slot4.insert(0, str(latest_price))

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
        for attr in ("_timer_id", "_clock_timer_id"):
            if hasattr(self, attr):
                try:
                    self.after_cancel(getattr(self, attr))
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

    def _recompute_lot_state(self):
        """FIFO sells by date; 满7日 always measured against today's date."""
        self._sync_full_from_ui()
        if not self._full_rows_data:
            return

        today = datetime.now().date()
        confirm_n = 2 if self.is_qdii_var.get() else 1
        sell_i = self.sell_col_index
        mature_i = self.mature_col_index
        share_i = 3

        indexed = list(enumerate(self._full_rows_data))
        indexed.sort(
            key=lambda item: (
                self._parse_row_date(item[1]) or datetime.min.date(),
                item[0],
            )
        )

        lots = []
        results = {}

        for orig_i, row in indexed:
            # Ensure row width
            while len(row) < len(self.headers):
                if len(row) == 7:
                    row.append("0")
                else:
                    row.append("-")

            as_of = self._parse_row_date(row)
            if as_of is None:
                results[orig_i] = "-"
                row[mature_i] = "-"
                continue

            sell = self.safe_float(row[sell_i])
            if sell > 0:
                remaining_sell = sell
                for lot in lots:
                    if remaining_sell <= 0:
                        break
                    if lot["confirm"] <= as_of and lot["remain"] > 0:
                        take = min(lot["remain"], remaining_sell)
                        lot["remain"] -= take
                        remaining_sell -= take

            bought = self.safe_float(row[share_i])
            if bought > 0:
                confirm = self.add_trading_days(as_of, confirm_n)
                lots.append({"confirm": confirm, "remain": bought})

            # 满7日：相对「今天」实时判断，不是相对行日期
            mature = sum(
                lot["remain"]
                for lot in lots
                if lot["remain"] > 0 and (today - lot["confirm"]).days >= 7
            )
            if abs(mature - round(mature)) < 1e-9:
                mature_str = str(int(round(mature)))
            else:
                mature_str = str(round(mature, 2))
            results[orig_i] = mature_str
            row[mature_i] = mature_str

        n_visible = len(self.rows)
        for i, row_widgets in enumerate(self.rows):
            if i >= len(self._full_rows_data):
                break
            val = results.get(i, self._full_rows_data[i][mature_i])
            self._set_entry_value(row_widgets[mature_i], val, readonly=True)

        if self._history_collapsed and self._hidden_rows_data is not None:
            self._hidden_rows_data = [
                list(r) for r in self._full_rows_data[n_visible:]
            ]

    def save_data(self):
        self._sync_full_from_ui()
        data = {
            "top_info": {
                "fund_name": self.top_slot2.get(),
                "real_time_price": self.top_slot4.get(),
                "is_qdii": bool(self.is_qdii_var.get()),
            },
            "rows": self._full_rows_data,
        }

        with open(self.save_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _months_ago(from_date, months):
        year = from_date.year
        month = from_date.month - months
        while month <= 0:
            month += 12
            year -= 1
        day = min(from_date.day, calendar.monthrange(year, month)[1])
        return from_date.replace(year=year, month=month, day=day)

    @staticmethod
    def _parse_row_date(row_data):
        try:
            return datetime.strptime(str(row_data[0])[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    def _normalize_row(self, row):
        """Pad legacy 6/7/8-col rows up to current header width."""
        if not isinstance(row, list):
            return None
        expected = len(self.headers)
        if len(row) > expected:
            return None
        out = list(row)
        while len(out) < expected:
            if len(out) == 6:
                out.append("-")  # 至今涨幅
            elif len(out) == 7:
                out.append("0")  # 卖出份额
            else:
                out.append("-")  # 满7日剩余等
        return out

    def _extract_visible_rows_data(self):
        rows_data = []
        for row in self.rows:
            row_data = []
            for cell in row:
                if isinstance(cell, DateEntry):
                    val = cell.get_date().strftime("%Y-%m-%d")
                elif isinstance(cell, (tk.Entry, ttk.Combobox)):
                    val = cell.get()
                elif isinstance(cell, tk.Label):
                    val = cell["text"]
                else:
                    val = ""
                row_data.append(val)
            rows_data.append(row_data)
        return rows_data

    def _sync_full_from_ui(self):
        visible = self._extract_visible_rows_data()
        if self._history_collapsed and self._hidden_rows_data:
            self._full_rows_data = visible + self._hidden_rows_data
        else:
            self._full_rows_data = visible

    def _split_visible_and_hidden(self, rows_data):
        """Return (visible, hidden). Hide rows older than HISTORY_MONTHS;
        if none remain, keep the FALLBACK_VISIBLE_ROWS most recent by date.
        """
        if not rows_data:
            return [], []

        cutoff = self._months_ago(datetime.now().date(), HISTORY_MONTHS)
        recent = []
        older = []
        for row in rows_data:
            d = self._parse_row_date(row)
            if d is None or d >= cutoff:
                recent.append(row)
            else:
                older.append(row)

        if recent:
            return recent, older

        # All older than cutoff: keep the N most recent by date (file order among them)
        indexed = list(enumerate(rows_data))
        indexed.sort(
            key=lambda item: self._parse_row_date(item[1]) or datetime.min.date(),
            reverse=True,
        )
        keep_indices = {idx for idx, _ in indexed[:FALLBACK_VISIBLE_ROWS]}
        visible = [row for i, row in enumerate(rows_data) if i in keep_indices]
        hidden = [row for i, row in enumerate(rows_data) if i not in keep_indices]
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

                valid_rows = []
                for row in rows_data:
                    normalized = self._normalize_row(row)
                    if normalized is not None:
                        valid_rows.append(normalized)
                self._full_rows_data = valid_rows

                fund_name = top_info.get("fund_name", "") or ""
                self.top_slot2.delete(0, tk.END)
                self.top_slot2.insert(0, fund_name)

                self.top_slot4.delete(0, tk.END)
                self.top_slot4.insert(0, top_info.get("real_time_price", ""))

                self.top_slot5.delete(0, tk.END)

                self.is_qdii_var.set(bool(top_info.get("is_qdii", False)))

                visible, hidden = self._split_visible_and_hidden(self._full_rows_data)
                self._hidden_rows_data = hidden
                self._history_collapsed = bool(hidden)
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
            self.render_action_buttons()
            self._recompute_lot_state()
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))

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
