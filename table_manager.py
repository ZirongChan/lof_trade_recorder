import os
import json

import tkinter as tk
from tkinter import ttk
from tkinter import simpledialog
from tkcalendar import DateEntry

from trade_sheet import TradeSheet, BASE_HEADERS
import xalpha_tool

class TableManager(tk.Tk):
    def __init__(self):
        super().__init__()
        
        # App title
        self.title("LOF套利记账本")
        self.geometry("1100x600")
        self.minsize(980, 500)
        
        # 基础表头；赎回分段列由 TradeSheet 按基金 feeinfo 动态追加
        self.headers = list(BASE_HEADERS)

        # 修改顺序
        # 创建notebook
        self.notebook = ttk.Notebook(self)
        # 创建控制面板
        self.control_frame = tk.Frame(self)

        # 先pack notebook
        self.notebook.pack(expand=True, fill='both')  
        # 再pack控制面板，这样控制面板就会在notebook的下方
        self.control_frame.pack(side='bottom', fill='x') # 修改为底部定位

        # 原有添加按钮
        self.add_sheet_button = tk.Button(
            self.control_frame,
            text="+ 记录新基金交易",
            command=self.add_new_sheet,
            fg= "blue")
        self.add_sheet_button.pack(side='left', padx=5, pady=5)

        # 新增删除当前标签页按钮
        self.del_sheet_button = tk.Button(
            self.control_frame,
            text="- 删除当前基金表单",
            command=self.delete_current_sheet,
            fg="red"
        )
        self.del_sheet_button.pack(side='left', padx=5, pady=5)

        # Dictionary to keep track of sheets and their names
        self.sheets = {}
        self.sheet_count = 0

        # Add the initial sheet
        self.load_existing_sheets()

        # force initial resize so the bottom buttons appear
        self.after_idle(lambda: self.on_tab_change(None))

        # Bind double-click event to the rename_sheet method
        self.notebook.bind("<Double-1>", self.rename_sheet)

        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_change)

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def on_closing(self):
        for name, table in self.sheets.items():
            table.save_data()
        self.destroy()

    def add_new_sheet(self):
        self.sheet_count += 1
        sheet_name = f"Sheet {self.sheet_count}"
        
        # 创建新的容器框架并配置布局
        frame = tk.Frame(self.notebook)

        self.notebook.add(frame, text=sheet_name)

        # 创建交易表单时需要传递正确的父容器（TradeSheet.__init__ 内已 load_data）
        trade_sheet = TradeSheet(frame, self.headers, sheet_name)
        trade_sheet.pack(expand=True, fill='both')

        self.update_idletasks()

        # 将新表单添加到notebook
        self.notebook.select(len(self.notebook.tabs())-1)  # 切换到新建标签页
        self.notebook.event_generate("<<NotebookTabChanged>>")  # 触发标签切换事件
        
        # Store the reference
        self.sheets[sheet_name] = trade_sheet

    def rename_sheet(self, event):
        # Identify which tab was double-clicked
        clicked_tab = self.notebook.index(f"@{event.x},{event.y}")
        current_name = self.notebook.tab(clicked_tab, "text")
        new_name = simpledialog.askstring("Rename Sheet", "输入新基金代码:", initialvalue=current_name)
    
        if new_name and (len(new_name) != 6 or not new_name.isdigit()):
            tk.messagebox.showerror("错误", "基金代码必须为6位数字！")
            return
    
        if new_name and new_name not in self.sheets:
            self.notebook.tab(clicked_tab, text=new_name)
            self.sheets[new_name] = self.sheets.pop(current_name)
            self.sheets[new_name].sheet_name = new_name
            self.sheets[new_name].save_path = os.path.join("data", f"{new_name}.json")

            # Update top_slot2 to show the new sheet name
            sheet = self.sheets[new_name]
            if hasattr(sheet, "top_slot2"):

                # get fund name in chinese
                fund_name = xalpha_tool.get_fund_name(new_name)
                # valid fund code, write to the corresponding unit
                sheet.top_slot2.delete(0, tk.END)
                sheet.top_slot2.insert(0, fund_name)

                sheet.reload_redeem_tiers_async()
                sheet.update_price()
                sheet.update_profits()

    def load_existing_sheets(self):
        os.makedirs("data", exist_ok=True)

        valid_sheets = 0
        # max_height = 0

        for file in os.listdir("data"):
            if file.endswith(".json"):
                sheet_name = os.path.splitext(file)[0]

                # Optional: check for valid sheet name (e.g., 6-digit fund code)
                if not sheet_name.isdigit():
                    print(f"Skipping file with invalid sheet name: {file}")
                    continue

                try:
                    # Always create the sheet and let it decide if the data is valid
                    frame = tk.Frame(self.notebook)
                    trade_sheet = TradeSheet(frame, self.headers, sheet_name)

                    # If it loaded any data rows, count it as valid
                    if trade_sheet.rows:
                        self.notebook.add(frame, text=sheet_name)
                        self.sheets[sheet_name] = trade_sheet
                        self.sheet_count += 1
                        valid_sheets += 1

                        # # get the height of each sheet
                        # height = sheet.get_total_height()
                        # if height > max_height:
                        #     max_height = height

                    else:
                        print(f"Sheet '{sheet_name}' has no valid rows — skipping tab.")

                    trade_sheet.pack(expand=True, fill="both")
                    
                except Exception as e:
                    print(f"Error loading sheet '{sheet_name}': {e}")
            
            # debug with only one sheet loaded
            # break

        # If no valid sheets were loaded, create a blank one
        if valid_sheets == 0:
            print("No valid sheets found — creating a new one.")
            self.add_new_sheet()
       
    def _active_sheet(self):
        tab = self.notebook.select()
        if not tab:
            return None
        name = self.notebook.tab(tab, "text")
        return self.sheets.get(name)

    def fit_window_to_active_sheet(self):
        """按表格需求宽度调整窗口（赎回列隐藏 / 顶栏收紧后应收窄）。"""
        sheet = self._active_sheet()
        need_w = 980
        if sheet is not None and hasattr(sheet, "preferred_window_width"):
            need_w = int(sheet.preferred_window_width())
        need_w = max(980, min(need_w, 1800))
        self.minsize(980, 500)
        self.update_idletasks()
        cur_w = self.winfo_width()
        cur_h = max(self.winfo_height(), 500)
        # 对齐到表格宽度：偏窄拉宽，偏宽（多出 >40px）收窄
        if cur_w < need_w or cur_w > need_w + 40:
            self.geometry(f"{need_w}x{cur_h}")

    def on_tab_change(self, event):
        # 表格在 sheet 内滚动；宽度随当前赎回列数自适应
        self.fit_window_to_active_sheet()

    def delete_current_sheet(self):
        # 获取当前选中的标签页
        current_tab = self.notebook.select()
        if not current_tab:
            tk.messagebox.showinfo("提示", "没有可删除的标签页")
            return

        # 获取标签页信息
        tab_index = self.notebook.index(current_tab)
        sheet_name = self.notebook.tab(current_tab, "text")

        # 确认对话框
        if not tk.messagebox.askyesno("确认", f"确定要永久删除 {sheet_name} 的记录吗？"):
            return

        try:
            # 1. 删除数据文件
            file_path = os.path.join("data", f"{sheet_name}.json")
            if os.path.exists(file_path):
                os.remove(file_path)
            
            # 2. 从notebook移除标签页
            self.notebook.forget(current_tab)
            
            # 3. 删除内存中的引用
            if sheet_name in self.sheets:
                del self.sheets[sheet_name]
            
            # 4. 如果删除的是最后一个标签页，自动创建新标签页
            if not self.notebook.tabs():
                self.add_new_sheet()
                
        except Exception as e:
            tk.messagebox.showerror("错误", f"删除失败: {str(e)}")