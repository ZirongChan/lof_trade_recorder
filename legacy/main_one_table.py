import tkinter as tk
from trade_table import TradeTable

if __name__ == "__main__":
    
    root = tk.Tk()
    root.title("Trading Table App")

    headers = ["Date", "Fund Code", "Fund Name", "Net Value", "Buy in Currency", "Bought Share", "Fee", "Total Spent", "Cost per Share"]
    table = TradeTable(root, headers)
    table.pack(expand=True, fill='both')

    # Save on close
    def on_closing():
        table.save_data()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    root.mainloop()

