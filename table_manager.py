import os
import json

import tkinter as tk
from tkinter import ttk
from tkinter import simpledialog
from tkcalendar import DateEntry

from trade_sheet import TradeSheet
import xalpha_tool

class TableManager(tk.Tk):
    def __init__(self):
        super().__init__()
        
        # App title
        self.title("LOF套利记账本")
        
        # create headers
        self.headers = ["日期", "净值", "申购金额", "到账份额", "申购成本/份", "预估利润"]

        # redemption: 赎回 in share

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
        
        frame = tk.Frame(self.notebook)
        self.notebook.add(frame, text=sheet_name)
        
        # 创建交易表单
        trade_sheet = TradeSheet(frame, self.headers, sheet_name)
        trade_sheet.pack(expand=True, fill='both')

        # dynamicly determine the height of table based on the sheet
        self.update_idletasks()

         # 重点添加以下两行
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
                    trade_sheet.pack(expand=True, fill="both")

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

                except Exception as e:
                    print(f"Error loading sheet '{sheet_name}': {e}")

        # If no valid sheets were loaded, create a blank one
        if valid_sheets == 0:
            print("No valid sheets found — creating a new one.")
            self.add_new_sheet()
       
    def on_tab_change(self, event):
        current_tab = self.notebook.select()
        if not current_tab: 
            return
        
        sheet_name = self.notebook.tab(current_tab, "text")
        sheet = self.sheets.get(sheet_name)
        
        if sheet:
            # 强制更新布局计算
            self.update_idletasks()
            sheet.update_idletasks()
            
            # 计算总高度 = 控制栏高度 + 表格实际高度 + 安全边距
            control_height = self.control_frame.winfo_height()
            sheet_height = sheet.winfo_reqheight()
            
            # 新增滚动条高度补偿（约20像素）
            scroll_compensation = 20 if sheet.has_vertical_scroll() else 0
            
            # 更新窗口高度（保持当前宽度）
            total_height = control_height + sheet_height + scroll_compensation + 40
            self.geometry(f"{self.winfo_width()}x{total_height}")
            
            # 设置最小高度保证按钮可见
            self.minsize(800, 400)

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