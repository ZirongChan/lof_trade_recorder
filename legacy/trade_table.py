import os
import json

import tkinter as tk
from tkinter import ttk
from tkinter import simpledialog
from tkcalendar import DateEntry

from datetime import datetime, timedelta

# from xalpha_tool import calculate_result
import xalpha_tool

# This is for a single table that u fill all the infos

class TradeTable(tk.Frame):
    def __init__(self, parent, headers, sheet_name):
        super().__init__(parent)
        self.headers = headers
        self.sheet_name = sheet_name
        self.rows = []
        self.cell_width = 120

        os.makedirs("data", exist_ok=True)
        self.save_path = os.path.join("data", f"{sheet_name}.json")

        fixed_width = len(headers) * self.cell_width

        self.canvas = tk.Canvas(self, width=fixed_width)
        self.table_frame = tk.Frame(self.canvas)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas_window = self.canvas.create_window((0, 0), window=self.table_frame, anchor='nw')
        self.table_frame.bind("<Configure>", self._on_frame_configure)

        self.load_data()

        self.draw_table()

    def _on_frame_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.config(height=self.table_frame.winfo_reqheight())

    def draw_table(self):
        for i, header in enumerate(self.headers):
            lbl = tk.Label(self.table_frame, text=header, borderwidth=1, relief="solid", width=15, anchor='center')
            lbl.grid(row=0, column=i, sticky="nsew")

    def add_row_on_bottom(self, prefill=None):
        row_index = len(self.rows) + 1
        row_widgets = []

        # Auto-prefill from previous row if no manual prefill
        if prefill is None and self.rows:
            last_row = self.rows[-1]
            prefill = {}

            try:
                # Date: add 1 day
                prev_date = last_row[0].get_date()
                next_date = prev_date + timedelta(days=-1)
                prefill["Date"] = next_date.strftime('%Y-%m-%d')
            except Exception as e:
                print("Could not parse previous date:", e)

            try:
                # Fund Code: copy from previous
                prefill["Fund Code"] = last_row[1].get()
            except Exception as e:
                print("Could not copy fund code:", e)

        for col_index, header in enumerate(self.headers):

            if header == "Date":
                cell = DateEntry(self.table_frame, width=12, date_pattern='yyyy-mm-dd', justify = 'center')
                
                if prefill and header in prefill:
                    cell.set_date(prefill[header])

                cell.bind("<Return>", lambda e, r=row_index: self.update_result(r))
                cell.bind("<FocusOut>", lambda e, r=row_index: self.update_result(r))

            elif header == "Buy in Currency":
                cell = tk.Entry(self.table_frame, width=12, justify='center')
                # cell = ttk.Combobox(self.table_frame, values=["On", "Off"], state="readonly", width=10) #下拉框的设计
                if prefill and header in prefill:
                    cell.insert(0, prefill[header])
                else:
                    cell.insert(0, "0")
                
                cell.bind("<Return>", lambda e, r=row_index: self.update_result(r))
                # cell.bind("<FocusOut>", lambda e, r=row_index: self.update_result(r))

            else:
                cell = tk.Entry(self.table_frame, width=12, justify='center')
                if prefill and header in prefill:
                    cell.insert(0, prefill[header])
                else:
                    cell.insert(0, "")

                cell.bind("<Return>", lambda e, r=row_index: self.update_result(r))
                # cell.bind("<FocusOut>", lambda e, r=row_index: self.update_result(r))

            cell.grid(row=row_index, column=col_index, sticky="nsew")
            row_widgets.append(cell)

        self.rows.append(row_widgets)
        self.render_add_button(row_index + 1)

    def add_row_on_top(self, prefill=None):

        # Auto-prefill from previous row if no manual prefill
        if prefill is None and self.rows:
            last_row = self.rows[0]
            prefill = {}

            try:
                # Date: add 1 day
                prev_date = last_row[0].get_date()
                next_date = prev_date + timedelta(days=1)
                prefill["Date"] = next_date.strftime('%Y-%m-%d')
            except Exception as e:
                print("Could not parse previous date:", e)

            try:
                # Fund Code: copy from previous
                prefill["Fund Code"] = last_row[1].get()
            except Exception as e:
                print("Could not copy fund code:", e)

        # Shift the existing rows down by one row
        for i, existing_row in enumerate(self.rows):
            for j, widget in enumerate(existing_row):
                widget.grid_configure(row=i + 2)

        # create new one
        row_index = 1
        row_widgets = []

        for col_index, header in enumerate(self.headers):
            if header == "Date":
                cell = DateEntry(self.table_frame, width=12, date_pattern='yyyy-mm-dd', justify = 'center')
                if prefill and header in prefill:
                    cell.set_date(prefill[header])

                cell.bind("<Return>", lambda e, r=row_index: self.update_result(r))
                cell.bind("<FocusOut>", lambda e, r=row_index: self.update_result(r))

            elif header == "Buy in Currency":
                cell = tk.Entry(self.table_frame, width=12, justify='center')
                if prefill and header in prefill:
                    cell.insert(0, prefill[header])
                else:
                    cell.insert(0, "0")

                cell.bind("<Return>", lambda e, r=row_index: self.update_result(r))
                # cell.bind("<FocusOut>", lambda e, r=row_index: self.update_result(r))

            else:
                cell = tk.Entry(self.table_frame, width=12, justify='center')
                if prefill and header in prefill:
                    cell.insert(0, prefill[header])

                cell.bind("<Return>", lambda e, r=row_index: self.update_result(r))
                # cell.bind("<FocusOut>", lambda e, r=row_index: self.update_result(r))

            cell.grid(row=row_index, column=col_index, sticky="nsew")
            row_widgets.append(cell)

        self.rows.insert(0, row_widgets) # insert in the first row
        self.render_add_button(len(self.rows) + 1) # shift the add_button down by one row

    def render_add_button(self, row):
        if hasattr(self, 'add_button'):
            self.add_button.destroy()

        self.add_button = tk.Button(self.table_frame, text="Add Row", command=self.add_row_on_top) # add row always on the top, can be switch to on the bottom
        self.add_button.grid(row=row, column=0, columnspan=len(self.headers), sticky="ew", pady=5)

    def update_result(self, row_index):
        if row_index > len(self.rows):
            return

        row = self.rows[row_index - 1]
        try:            
            # get the date selected and the input fund code
            date_str = row[0].get()
            # print(date_str)

            fund_code = row[1].get() if isinstance(row[1], tk.Entry) else row[1].get()

            # check validation of the code
            if not len(fund_code) == 6 or not fund_code.isdigit():
                print("Invalid Fund Code given, plz check and re-try with a 6-digit code.\n")
                return

            # get fund name in chinese
            fund_name = xalpha_tool.get_fund_name(fund_code)
            if fund_name == None:
                print("Wrong Fund Code given, plz check and re-try.\n")
                return
            else:
                # valid fund code, write to the corresponding unit
                # erase the current value
                row[2].delete(0, tk.END)
                # insert target value
                row[2].insert(0, fund_name)

            # update the net value of fund in corresponding date
            date_price_infos = xalpha_tool.fetch_otc_fund_net_values(fund_code, 3)
            # print(date_price_infos)

            net_value = date_price_infos[date_str]
            if net_value is not None:
                # print("date matches.\n")
                row[3].delete(0, tk.END)
                row[3].insert(0, net_value)
            else:
                print("date does not match, no net value can be update.\n")
                return
            
            # trading operations
            #### buy #### 
            buy_money_amount = row[4].get()
            
            print(buy_money_amount)

            if not buy_money_amount.isdigit():
                print("money amount for buying is not right, plz check and re-try with reasonable value.\n")
                return

            # buy_res = xalpha_tool.changnei_shengou(fund_code, 1812, '2025-04-03') # for test
            buy_res = xalpha_tool.changnei_shengou(fund_code, int(buy_money_amount), date_str)

            # fill in table units
            row[5].delete(0, tk.END)
            row[5].insert(0, buy_res["share_amount_bought"])
            row[6].delete(0, tk.END)
            row[6].insert(0, buy_res["fee"])
            row[7].delete(0, tk.END)
            row[7].insert(0, buy_res["total_money"])
            row[8].delete(0, tk.END)
            row[8].insert(0, buy_res["cost_per_share"])


            #### sell ####
            # you have to give the sell price in order to calculate the results

        except Exception as e:
            print(f"Error calculating result for row {row_index}: {e}")
            return

    def update_all_results(self):
        for i in range(1, len(self.rows) + 1):
            self.update_result(i)

    def save_data(self):
        data = []
        for row in self.rows:
            row_data = []
            for cell in row:
                if isinstance(cell, (tk.Entry, ttk.Combobox)):
                    row_data.append(cell.get())
                elif isinstance(cell, DateEntry):
                    row_data.append(cell.get_date().strftime('%Y-%m-%d'))
                elif isinstance(cell, tk.Label):
                    row_data.append(cell["text"])
            data.append(row_data)
        with open(self.save_path, "w") as f:
            json.dump(data, f, indent=2)

    def load_data(self):
        if not os.path.exists(self.save_path):
            for _ in range(3):
                self.add_row_on_bottom(prefill=None)
            return
        
        try:
            print("Trying to load:", self.save_path)
            
            # Hide the canvas temporarily to prevent visible updating
            self.canvas.pack_forget()

            with open(self.save_path, "r", encoding="utf-8") as f:
                raw = f.read()
                # print("Raw file content:", raw)

                data = json.loads(raw)

            if not isinstance(data, list) or not data:
                raise ValueError("Data is not a non-empty list")

            for row_data in data:
                # Basic validation — check if it has the correct number of fields
                if not isinstance(row_data, list) or len(row_data) != len(self.headers):
                    raise ValueError("Invalid row format")

                prefill = dict(zip(self.headers, row_data))
                self.add_row_on_bottom(prefill=prefill)

            # End of data loading: show the canvas again
            self.canvas.pack(side="left", fill="both", expand=True)
            self.update_all_results()  # if you need to update calculations on all rows
            self.canvas.update_idletasks()  # ensure everything is drawn

        except Exception as e:
            print(f"Error loading saved data: {e}")
            # Fall back to default empty table
            for _ in range(3):
                self.add_row_on_bottom(prefill=None)
