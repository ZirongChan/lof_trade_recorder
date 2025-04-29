import os
import json

import threading

import tkinter as tk
from tkinter import ttk
from tkinter import simpledialog
from tkcalendar import DateEntry

from datetime import datetime, timedelta

# from xalpha_tool import calculate_result
import xalpha_tool

# 在文件顶部添加全局配置
DEBUG_TIMING = False  # 计时调试开关

# Add at the top with other imports
import time

class TradeSheet(tk.Frame):
    def __init__(self, parent, headers, sheet_name):
        if DEBUG_TIMING:
            # No longer needs 'import time' here since it's imported at top
            self._init_start = time.perf_counter()
        
        super().__init__(parent)

        self.headers = headers
        self.sheet_name = sheet_name
        self.rows = []

        self.cell_width = 12 # 固定单元格宽度

        self._configure_style()

        # for the real-time trading price，初始化为0
        self.rt_price = 0

        # 预估利润的单元格在行中索引
        self.profit_col_index = len(self.headers) - 1

        # 选中行的索引，用于高亮功能，及删除行功能
        self.selected_row_index = None

        os.makedirs("data", exist_ok=True)
        self.save_path = os.path.join("data", f"{sheet_name}.json")

        self.canvas = tk.Canvas(self, width=len(headers) * self.cell_width)
        self.table_frame = tk.Frame(self.canvas)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        # self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.pack(side="left", fill="both")

        self.scrollbar.pack(side="right", fill="y")
        self.canvas_window = self.canvas.create_window((0, 0), window=self.table_frame, anchor='nw')
        self.table_frame.bind("<Configure>", self._on_frame_configure)

        if DEBUG_TIMING:
            self._init_loaded = time.perf_counter()
            
        # 在开始加载前暂停布局计算（新增）
        self.table_frame.grid_propagate(False)
        
        # 绘制顶部信息行
        self.draw_top_info_row()

        # 绘制表格
        self.draw_table()

        # 加载数据
        self.load_data() # 此时数据行会添加到表头下方
        
        # 恢复布局计算（新增）
        self.table_frame.grid_propagate(True)
        self.canvas.pack(side="left", fill="both")

        # 移除原位置的 canvas.pack()
        self.update_all_results()
        self.canvas.update_idletasks()

        if DEBUG_TIMING:
            self._init_loaded_done = time.perf_counter()
            print(f"数据加载耗时: {(self._init_loaded_done - self._init_loaded)*1000:.2f}ms")

        self.table_frame.update_idletasks()  # 恢复布局计算

        # 更新实时交易价格
        self.update_price()

        # 更新预测利润
        self.update_profits()
    
    def _on_frame_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.config(height=self.table_frame.winfo_reqheight())

    def get_req_width(self):
        # make sure all measurements are up to date
        self.update_idletasks()
        # width of the whole frame that holds your grid of headers+rows
        return self.table_frame.winfo_reqwidth()

    def draw_table(self):
        for i, header in enumerate(self.headers):
            lbl = tk.Label(self.table_frame, text=header, borderwidth=1, relief="solid", width=self.cell_width, anchor='center')
            lbl.grid(row=1, column=i, sticky="nsew")  # Shift headers down to row 1

    def add_row_on_bottom(self, prefill=None):
        row_index = len(self.rows) + 2
        row_widgets = []

        # Auto-prefill from previous row if no manual prefill
        if prefill is None and self.rows:
            last_row = self.rows[-1]
            prefill = {}

            try:
                # Date: minus 1 day
                prev_date = last_row[0].get_date()
                next_date = prev_date + timedelta(days=-1)
                # prefill["Date"] = next_date.strftime('%Y-%m-%d')
                prefill["日期"] = next_date.strftime('%Y-%m-%d')

            except Exception as e:
                print("Could not parse previous date:", e)

        for col_index, header in enumerate(self.headers):

            if header == "Date" or header == "日期":
                cell = DateEntry(self.table_frame, width=self.cell_width, date_pattern='yyyy-mm-dd', justify = 'center')
                
                if prefill and header in prefill:
                    cell.set_date(prefill[header])

                cell.bind("<Return>", self.handle_entry_return)
                # cell.bind("<Return>", lambda e, r=row_index: self.update_result(r))
                # cell.bind("<FocusOut>", lambda e, r=row_index: self.update_result(r))

                # 新增：绑定当前行高亮
                cell.bind("<FocusIn>", lambda e, w=cell: self.highlight_selected_row(w))

            elif header == "Sub. in Currency" or header == "申购金额":
                cell = tk.Entry(self.table_frame, width=self.cell_width, justify='center')
                if prefill and header in prefill:
                    cell.insert(0, prefill[header])
                else:
                    cell.insert(0, "0")

                # 原本的实现，entry控件绑定的行号没有动态更新
                # 增或减一行时，行号不会更新，导致update_result()的输入行号错误
                # cell.bind("<Return>", lambda e, r=row_index: self.update_result(r))
                
                # 修改后，按下回车或者鼠标移到其他位置，即可尝试更新整行数据
                cell.bind("<Return>", self.handle_entry_return)
                cell.bind("<FocusOut>", self.handle_entry_return)
                
                # 新增：绑定当前行高亮
                cell.bind("<FocusIn>", lambda e, w=cell: self.highlight_selected_row(w))
            else:
                cell = tk.Entry(self.table_frame, width=self.cell_width, justify='center')
                if prefill and header in prefill:
                    cell.insert(0, prefill[header])
                else:
                    cell.insert(0, "-")

                # 修改后，按下回车或者鼠标移到其他位置，即可尝试更新整行数据
                cell.bind("<Return>", self.handle_entry_return)
                cell.bind("<FocusOut>", self.handle_entry_return)

                # 新增：绑定当前行高亮
                cell.bind("<FocusIn>", lambda e, w=cell: self.highlight_selected_row(w))
            
            cell.grid(row=row_index, column=col_index, sticky="nsew")
            row_widgets.append(cell)

        self.rows.append(row_widgets)
        self.render_add_button(row_index + 1)

    def add_row_on_top(self, prefill=None):
        # Auto-prefill from previous row if no manual prefill
        if prefill is None and self.rows:
            # Bug 修复：索引从 0 开始
            last_row = self.rows[0] 
            prefill = {}

            try:
                # Date: add 1 day
                prev_date = last_row[0].get_date()
                next_date = prev_date + timedelta(days=1)
                # prefill["Date"] = next_date.strftime('%Y-%m-%d')
                prefill["日期"] = next_date.strftime('%Y-%m-%d')

            except Exception as e:
                print("Could not parse previous date:", e)

        # Shift the existing rows down by one row
        for i, existing_row in enumerate(self.rows):
            for j, widget in enumerate(existing_row):
                widget.grid_configure(row=i + 3)

        # create new one
        row_index = 2
        row_widgets = []

        for col_index, header in enumerate(self.headers):
            if header == "Date" or header == "日期":
                cell = DateEntry(self.table_frame, width=self.cell_width, date_pattern='yyyy-mm-dd', justify = 'center')
                if prefill and header in prefill:
                    cell.set_date(prefill[header])
                
                cell.bind("<Return>", self.handle_entry_return)

                # 新增：绑定当前行高亮
                cell.bind("<FocusIn>", lambda e, w=cell: self.highlight_selected_row(w))

            elif header == "Sub. in Currency" or header == "申购金额":
                cell = tk.Entry(self.table_frame, width=self.cell_width, justify='center')
                if prefill and header in prefill:
                    cell.insert(0, prefill[header])
                else:
                    cell.insert(0, "0")

                # 修改后，按下回车或者鼠标移到其他位置，即可尝试更新整行数据
                cell.bind("<Return>", self.handle_entry_return)
                cell.bind("<FocusOut>", self.handle_entry_return)
                                
                # 新增：绑定当前行高亮
                cell.bind("<FocusIn>", lambda e, w=cell: self.highlight_selected_row(w))
            else:
                cell = tk.Entry(self.table_frame, width=self.cell_width, justify='center')
                if prefill and header in prefill:
                    cell.insert(0, prefill[header])

                # 修改后，按下回车或者鼠标移到其他位置，即可尝试更新整行数据
                cell.bind("<Return>", self.handle_entry_return)
                cell.bind("<FocusOut>", self.handle_entry_return)

                # 新增：绑定当前行高亮 
                cell.bind("<FocusIn>", lambda e, w=cell: self.highlight_selected_row(w))

            cell.grid(row=row_index, column=col_index, sticky="nsew")
            row_widgets.append(cell)

        self.rows.insert(0, row_widgets) # insert in the first row
        self.render_add_button(len(self.rows) + 2) # shift the add_button down by one row

    # 在 TradeSheet 类中添加新的处理方法
    def handle_entry_return(self, event):
        """动态获取当前行号并更新结果"""
        widget = event.widget
        row = widget.grid_info()["row"]  # 获取控件所在的实际行号
        self.update_result(row)

    def delete_selected_row(self):
        # 检查是否有行被选中
        if self.selected_row_index is None:
            print("请先点击要删除行中的任意单元格")
            return  
        
        # 删除行内所有控件
        target_row = self.selected_row_index
        self.selected_row_index = None

        for widget in self.rows[target_row]:
            widget.destroy()
        
        # 从数据源移除行记录
        del self.rows[target_row]

        # 重新布局后续所有行 (关键修复)
        for update_row_idx in range(target_row, len(self.rows)):
            for widget in self.rows[update_row_idx]:
                current_info = widget.grid_info()
                # 确保行号是整数类型（添加类型转换）
                current_row = int(current_info["row"])
                # 更新行号时需要重新grid所有参数
                widget.grid(
                    row=current_row - 1,
                    column=current_info["column"],
                    sticky=current_info["sticky"]
                )
                # 强制立即更新布局（新增）
                widget.update_idletasks()

        # 强制刷新画布布局（修改为更可靠的更新方式）
        self.table_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.update_idletasks()
        
        # 修复2：更新按钮位置时使用正确的行数计算
        self.render_add_button(len(self.rows) + 2)
        
        # 强制刷新画布布局
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.update_profits()
        
        # 实时保存
        self.save_data()

        # # 调整窗口高度（彻底修改这部分）
        # root = self.winfo_toplevel()
        
        # # 计算表格实际需要的高度
        # table_height = self.table_frame.winfo_reqheight() 
        
        # # 获取窗口当前尺寸
        # win_width = root.winfo_width()
        # current_height = root.winfo_height()
        
        # # 动态计算新高度（增加50像素缓冲）
        # new_height = max(table_height + 150, current_height - 50)
        
        # # 添加安全限制（高度不低于400像素）
        # new_height = max(new_height, 400)
        
        # 更新窗口尺寸
        root = self.winfo_toplevel()
        root.update_idletasks()
        req_height = self.get_total_height() + root.control_frame.winfo_height() + 20
        root.geometry(f"{root.winfo_width()}x{max(req_height, 400)}")

    def has_vertical_scroll(self):
        # 检测是否存在垂直滚动条
        return self.canvas.yview() != (0.0, 1.0)
    
    def get_total_height(self):
        self.update_idletasks()
        # 计算实际渲染高度
        table_height = self.table_frame.winfo_reqheight()
        # 计算按钮行高度（包含间距）
        button_height = self.add_top_btn.winfo_height() + 10
        return table_height + button_height

    def _configure_style(self):
        """配置 ttk 按钮样式"""
        self.style = ttk.Style()
    
        # 定义样式名称，避免污染全局样式
        self.style.configure(
            "TradeSheet.TButton", 
            padding=2, 
            font=("Arial", 9)
        )

    def render_add_button(self, row):
        # ——— 1) 删除旧按钮 ———
        for attr in ('add_top_btn','add_bottom_btn','delete_row_btn','update_profits_btn'):
            if hasattr(self, attr):
                getattr(self, attr).destroy()
        
        total_cols = len(self.headers)
        # 计算每个按钮跨几列（向下取整）
        base = total_cols // 4
        rem = total_cols % 4

        # 2) 计算每个按钮应跨越多少列
        spans = [base + (1 if i < rem else 0) for i in range(4)]
        # 当 total_cols=6 时，spans = [2,2,1,1]

        # 3) 给所有列统一权重
        for c in range(total_cols):
            self.table_frame.columnconfigure(c, weight=1, uniform="btn_cols")

        # 创建4个按钮，水平排列
        self.add_top_btn = ttk.Button(
            self.table_frame, 
            text="↑ 顶部插入 ↑", 
            command=self.add_row_on_top,  # 绑定到顶部添加方法
            style="TradeSheet.TButton"  # 应用自定义样式
        )

        self.add_bottom_btn = ttk.Button(
            self.table_frame, 
            text="↓ 底部追加 ↓", 
            command=self.add_row_on_bottom,  # 绑定到底部添加方法
            style="TradeSheet.TButton"  # 应用自定义样式
        )

        self.delete_row_btn = ttk.Button(
            self.table_frame,
            text="✖ 删除本行 ✖",
            command=self.delete_selected_row,
            style="TradeSheet.TButton"
        )

        self.update_profits_btn = ttk.Button(
            self.table_frame,
            text="刷新利润",
            command=self.update_profits,
            style="TradeSheet.TButton"
        )
        
        # 5) 按照 spans 安排 grid 起始列
        col = 0
        for btn, span in zip((self.add_top_btn, self.add_bottom_btn, self.delete_row_btn, self.update_profits_btn), spans):
            btn.grid(row=row, column=col, columnspan=span, sticky="ew", pady=5)
            col += span
    
        # 6) 刷新布局
        self.table_frame.update_idletasks()

    def update_result(self, row_index):

        # yuanbao
        # 计算正确的索引：表格行号 - 2（因为数据行从第2行开始）
        row_index_in_list = row_index - 2
        
        # 检查索引是否越界
        if row_index_in_list < 0 or row_index_in_list >= len(self.rows):
            return
        row = self.rows[row_index_in_list]  # 正确获取行数据
        
        try:            
            # get the date selected and the input fund code
            date_str = row[0].get()
            # print(date_str)

            fund_code = self.sheet_name

            # check validation of the code
            if not len(fund_code) == 6 or not fund_code.isdigit():
                print("Invalid Fund Code given, plz check and re-try with a 6-digit code.\n")
                return

            # update the net value of fund in corresponding date

            # date_price_infos = xalpha_tool.fetch_otc_fund_net_values(fund_code, len(self.rows)+2)
            # net_value = date_price_infos.get(date_str)

            # alternatively
            net_value = xalpha_tool.fetch_otc_fund_net_value(fund_code, date_str)

            # 需要clear当前单元格的数据
            row[1].delete(0, tk.END) # 净值
            row[3].delete(0, tk.END) # 到账份额
            row[4].delete(0, tk.END) # 每份成本（含手续费）
            row[self.profit_col_index].delete(0, tk.END) # 预估利润

            # 插入默认值"-"作为初始化
            row[self.profit_col_index].insert(0, '-')
            
            if net_value is not None:
                # print("date matches.\n")
                row[1].insert(0, net_value)
            else:
                print("错误", f"{date_str} 无净值数据 for {fund_code}！\n")
                return
            
            # trading operations
            #### buy #### 
            buy_money_amount = row[2].get() # 申购金额
            
            # print(buy_money_amount)

            if not buy_money_amount.isdigit():
                print("money amount for buying is not right, plz check and re-try with reasonable value.\n")
                return

            # buy_res = xalpha_tool.changnei_shengou(fund_code, 1812, '2025-04-03') # for test
            buy_res = xalpha_tool.changnei_shengou(fund_code, int(buy_money_amount), date_str)

            # fill in table units
            row[3].delete(0, tk.END)
            row[3].insert(0, buy_res["share_amount_bought"])
            row[4].delete(0, tk.END)
            row[4].insert(0, buy_res["cost_per_share"])

            # update profit
            self.update_profit_cell(row)

            #### sell ####
            # you have to give the sell price in order to calculate the results

        except Exception as e:
            print(f"Error calculating result for row {row_index}: {e}")
            return

    def update_all_results(self):
        for i in range(1, len(self.rows) + 2):
            self.update_result(i)

        # self.after(5000, self.update_all_results)

    def update_price(self):
        try:
            self.top_slot4.delete(0, tk.END)
            self.top_slot4.insert(0, "-")

            if self.sheet_name.startswith("50"):
                # SH
                fund_code = "SH" + self.sheet_name
            elif self.sheet_name.startswith("16"):
                # SZ
                fund_code = "SZ" + self.sheet_name
            else:
                print(f"Invalid LOF code given, plz check and re-try.\n")
                return

            # # fetch real-time price via xalpha
            # latest_price = xalpha_tool.fetch_realtime_market_price(fund_code)
            # self.rt_price = latest_price

            # # show the price in the top_slot4
            # self.top_slot4.delete(0, tk.END)
            # self.top_slot4.insert(0, str(latest_price))

            # alternatively, show the percentage as well
            latest_info = xalpha_tool.fetch_realtime_stock_info(fund_code)
            if latest_info is not None:
                percentage = latest_info["percent"]

                latest_price = latest_info["current"]
                self.rt_price = latest_price

                # if latest_info['market'] is not None:
                #     print(latest_info['market'])

                self.top_slot4.delete(0, tk.END)
                self.top_slot4.insert(0, str(latest_price))

                self.top_slot5.delete(0, tk.END)
                self.top_slot5.insert(0, f"{percentage}%")
                if percentage > 0:
                    self.top_slot5.configure(fg="red")
                else:
                    self.top_slot5.configure(fg="green")

        except Exception as e:
            print("Price update failed:", e)
        
        finally:
            # 记录定时器ID
            self._timer_id = self.after(5000, self.update_price)

    def destroy(self):
        if hasattr(self, "_timer_id"):
            self.after_cancel(self._timer_id)  # 取消定时器
        super().destroy()
        
    def safe_float(self, value_or_widget):
        try:
            # Handle tk.Entry or other widgets
            if hasattr(value_or_widget, 'get'):
                raw = value_or_widget.get()
            else:
                raw = value_or_widget

            return float(raw.strip()) if raw.strip() else 0.0

        except (ValueError, TypeError):
            return 0.0

    def update_profits(self):
        for i in range(0, len(self.rows)):
            row = self.rows[i]  # 正确获取行数据
            self.update_profit_cell(row)

    def update_profit_cell(self, row):
        if isinstance(row[0], DateEntry):
            profit_cell = row[self.profit_col_index]
                
            if isinstance(profit_cell, tk.Entry):                    
                cost = self.safe_float(row[4].get())

                # check valid cost per share
                if cost > 0 and self.rt_price > 0:
                    profit_per_share = self.rt_price - cost

                    num_shares_bought = self.safe_float(row[3].get())

                    # 华宝的LOF卖出保底0.2手续费
                    profit = round(profit_per_share * num_shares_bought - 0.2, 2)
            
                    profit_cell.delete(0, tk.END)
                    profit_cell.insert(0, str(profit))

                    if profit > 0:
                        profit_cell.configure(fg="red")
                    else:
                        profit_cell.configure(fg="green")
                else:
                    profit_cell.delete(0, tk.END)
                    profit_cell.insert(0, "-")
                    profit_cell.configure(fg="blue")

    # yuanbao
    def save_data(self):
        # 构建包含顶部信息和行数据的字典
        data = {
            "top_info": {
                "fund_name": self.top_slot2.get(),
                "real_time_price": self.top_slot4.get()
            },
            "rows": []
        }

        # 遍历每一行并提取数据
        for row in self.rows:
            row_data = []
            for cell in row:
                if isinstance(cell, (tk.Entry, ttk.Combobox)):
                    val = cell.get()
                elif isinstance(cell, DateEntry):
                    val = cell.get_date().strftime('%Y-%m-%d')
                elif isinstance(cell, tk.Label):
                    val = cell["text"]
                else:
                    val = ""  # 处理未知控件类型
                row_data.append(val)
            data["rows"].append(row_data)

        # 写入JSON文件
        with open(self.save_path, "w") as f:
            json.dump(data, f, indent=2)

    def load_data(self):
        if not os.path.exists(self.save_path):
            # 初始化空数据和默认行
            self.top_slot2.insert(0, "")
            self.top_slot4.insert(0, "")
            self.top_slot5.insert(0, "")

            # 使用批量添加模式
            with self.disable_redraw():
                for _ in range(3):
                    self.add_row_on_bottom()

            self.canvas.pack(side="left", fill="both", expand=True)
            return
    
        try:
    
            # self.canvas.pack_forget()
            with open(self.save_path, "r", encoding="utf-8") as f:
                saved_data = json.load(f)
    
            # 兼容旧格式（纯行数据列表）
            if isinstance(saved_data, list):
                top_info = {"fund_name": "", "real_time_price": ""}
                rows_data = saved_data
            else:
                top_info = saved_data.get("top_info", {})
                rows_data = saved_data.get("rows", [])
    
            # 加载顶部信息（关键逻辑保留）
            fund_name = top_info.get("fund_name", "") 
            if not fund_name:
                fund_name = xalpha_tool.get_fund_name(self.sheet_name)
            
            self.top_slot2.delete(0, tk.END)
            self.top_slot2.insert(0, fund_name)
            
            self.top_slot4.delete(0, tk.END)
            self.top_slot4.insert(0, top_info.get("real_time_price", ""))

            self.top_slot5.delete(0, tk.END)
    
            # 使用批量加载模式（优化点）
            with self.disable_redraw():
                for row in rows_data:
                    if len(row) != len(self.headers):
                        continue
                    prefill = dict(zip(self.headers, row))
                    self.add_row_on_bottom(prefill=prefill)
            
            # 在加载完成后强制渲染按钮
            self.render_add_button(len(self.rows) + 2)
            self.canvas.pack(side="left", fill="both", expand=True)
            self.update_all_results()
            self.canvas.update_idletasks()
    
        except Exception as e:
            print(f"加载失败: {e}")
            # 失败时初始化空数据（关键异常处理保留）
            self.top_slot2.delete(0, tk.END)
            self.top_slot4.delete(0, tk.END)
            self.top_slot5.delete(0, tk.END)
            with self.disable_redraw():
                for _ in range(3):
                    self.add_row_on_bottom()
    
            # 异常处理后也需要渲染按钮
            self.render_add_button(len(self.rows) + 2)

            # 确保在异常处理后显示Canvas
            self.canvas.pack(side="left", fill="both", expand=True)
            self.canvas.update_idletasks()
    
    # 新增上下文管理器用于批量操作（添加）
    def disable_redraw(self):
        class Disabler():
            def __init__(self, frame):
                self.frame = frame
            def __enter__(self):
                self.frame._disable_redraw = True
                self.frame.update = lambda: None  # 禁用更新
            def __exit__(self, *args):
                self.frame._disable_redraw = False
                self.frame.update_idletasks()
        return Disabler(self.table_frame)

    def draw_top_info_row(self):
        # 统一使用表格总列数（6列）进行布局
        total_columns = len(self.headers)
        
        # 配置表格列均匀分布
        for i in range(total_columns):
            self.table_frame.columnconfigure(i, weight=1, uniform="colgroup")
        
        # 基金名称部分（占用前3列）
        self.top_slot1 = tk.Label(self.table_frame, text="基金名称", 
                                width=self.cell_width, anchor='center')
        self.top_slot1.grid(row=0, column=0, columnspan=1, sticky="nsew")
        
        self.top_slot2 = tk.Entry(self.table_frame, width=self.cell_width*2, justify="center", fg="blue")
        self.top_slot2.grid(row=0, column=1, columnspan=2, sticky="nsew")  # 占用2列
        
        # 实时价格部分（占用后3列）
        # 标题
        self.top_slot3 = tk.Label(self.table_frame, text="场内成交价",
                                width=self.cell_width, anchor='center')
        self.top_slot3.grid(row=0, column=3, columnspan=1, sticky="nsew")  # 占用1列
        
        # 价格
        self.top_slot4 = tk.Entry(self.table_frame, width=self.cell_width, justify="center", fg="blue")
        self.top_slot4.grid(row=0, column=4, columnspan=1, sticky="nsew")  # 占用1列
    
        # 百分比
        self.top_slot5 = tk.Entry(self.table_frame, width=self.cell_width, justify="center")
        self.top_slot5.grid(row=0, column=5, columnspan=1, sticky="nsew")  # 占用1列

    def get_add_button_height(self):
        if hasattr(self, "add_button"):
            self.add_button.update_idletasks()  # Ensure it’s been drawn
            return self.add_button.winfo_reqheight()
        return 0

    def highlight_selected_row(self, widget):
        # 先重置所有行的背景色
        for row in self.rows:
            for cell in row:
                try:
                    cell.configure(bg="white")
                except Exception:
                    pass
        
        # 然后设置当前行的背景色
        target_row = None
        for row_index, row in enumerate(self.rows):
            if widget in row:
                target_row = row_index
                self.selected_row_index = row_index
                break
        
        if target_row is not None:
            for cell in self.rows[target_row]:
                if isinstance(cell, tk.Entry) and not isinstance(cell, DateEntry):  # 确保只处理Entry控件
                    try:
                        cell.configure(bg="#e0e0ff")  # 使用更明显的浅蓝色高亮
                    except Exception as e:
                        print(f"无法设置单元格背景: {e}")

    def update_geometry(self):
        # 通知父容器更新布局
        if self.master and hasattr(self.master, 'update_idletasks'):
            self.master.update_idletasks()
        if self.winfo_toplevel() != self:
            self.winfo_toplevel().update_idletasks()
    
    def get_total_height(self):
        # 删除原有实现，改为动态计算
        self.update_idletasks()
        return self.table_frame.winfo_reqheight() + 50  # 仅返回表格自身高度