
import xalpha as xa       # 基金数据获取 
import tkinter as tk       # 基础GUI框架
from tkinter import ttk    # 增强型表格组件
import sqlite3            # 本地交易记录存储

print(xa.__version__)

# 1. 基金数据获取
def get_fund_data(code):
    fund = xa.fundinfo(code)
    print(f"基金 {code} 最新净值:", fund.price.iloc[-1])
    return fund

# 2. 溢价率计算
def calc_premium(code):
    fund = xa.fundinfo(code)
    nav = fund.price.iloc[-1]
    market_price = xa.get_rt(code)  # 可配置实时数据源
    premium = (market_price - nav) / nav
    print(f"实时溢价率: {premium:.2%}")
    return premium

if __name__ == "__main__":
    # print(calc_premium('SH501025'))
    # print(xa.get_rt('SH501025'))
    
    fund = xa.fundinfo('501025')
    nav = fund.price.iloc[-1]
    print(nav)
    
