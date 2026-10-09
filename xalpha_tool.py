from math import floor

import xalpha as xa
from datetime import datetime


# 返回最新的交易日的信息
def fetch_otc_fund_net_value_latest(code):
    """获取基金数据"""
    try:
        fund = xa.fundinfo(code)
        price_info = fund.price.iloc[-1]  # 最新净值

        netvalue = price_info['netvalue']
        date = price_info['date']

        # print(round(netvalue, 4))

        return {
            'net_value': netvalue,
            'date': date 
        }
    except Exception as e:
        return None

# 场外基金, over the counter fund, 返回过去n个工作日的信息
def fetch_otc_fund_net_values(code, num_days):
    """获取基金数据"""
    try:
        fund = xa.fundinfo(code)

        date_price_infos = {}

        for i in range(0, num_days):
            id = i + 1

            price_info = fund.price.iloc[-id]
            
            netvalue = price_info['netvalue']
            date = price_info['date'].strftime('%Y-%m-%d')

            date_price_infos[date] = netvalue

        return date_price_infos

    except Exception as e:
        return None

# 场外基金, over the counter fund, 返回指定时间的信息
def fetch_otc_fund_net_value(code, date):
    try:
        fund = xa.fundinfo(code)
        cnt = 0
        
        while 1:
            id = cnt + 1
            price_info = fund.price.iloc[-id]

            netvalue = price_info['netvalue']
            date_info = price_info['date'].strftime('%Y-%m-%d')

            if date_info == date:
                return netvalue
            else:
                cnt += 1
                if cnt > 100:
                    return None
    
    except Exception as e:
        return None

# 场内交易的基金的最新价格
def fetch_realtime_market_price(code):
    try:
        stock_info = xa.get_rt(code)

        market_price = stock_info['current']

        return market_price
    except Exception as e:
        return None

        # market_price = xa.get_rt(code)  # 市价（需配置数据源）
        # premium = (market_price - nav) / nav * 100  # 溢价率

# 场内交易的基金的最新价格 以及 涨跌百分比
# market_price = stock_info['current']
# change_percentage = stock_info['change_percentage']

def fetch_realtime_stock_info(code):
    try:
        stock_info = xa.get_rt(code)

        return stock_info
    except Exception as e:
        return None

def get_fund_name(code):
    try:
        fund = xa.fundinfo(code)
        return fund.name

    except Exception as e:
        return None

def subscribe_xalpha(code, money_amount, date):
    try:
        fund = xa.fundinfo(code)

        # 申购费，现在一般都打折的，按%来的
        actual_rate = fund.rate
        if actual_rate < 0.3:
            declared_rate = actual_rate * 10
        else:
            declared_rate = actual_rate
            actual_rate = declared_rate * 0.1
        
        # 预先扣除掉手续费(0.9目前看来是一个便于使用xalpha库的shengou函数的trick)
        money_to_buy = money_amount * (1 - declared_rate * 0.01 * 0.9)
        res = fund.shengou(value=money_to_buy, date=date)

        print(res)

    except Exception as e:
        return None

def subscribe(code, money_amount, date):
    try:
        fund = xa.fundinfo(code)

        # 申购费，现在一般都打折的，按%来的
        actual_rate = fund.rate
        if actual_rate < 0.3:
            declared_rate = actual_rate * 10
        else:
            declared_rate = actual_rate
            actual_rate = declared_rate * 0.1

        # 申购当天的净值（与 fetch_otc_fund_net_value 一致，限制回溯天数）
        net_val_to_buy = None
        for offset in range(1, 101):
            price_info = fund.price.iloc[-offset]
            val_date = price_info["date"].strftime("%Y-%m-%d")
            if val_date == date:
                net_val_to_buy = price_info["netvalue"]
                break
        if net_val_to_buy is None:
            return None

        # 预先扣除掉手续费
        money_to_buy = money_amount * (1 - declared_rate * 0.01)

        share_amount_bought = floor(money_to_buy / net_val_to_buy)
        if share_amount_bought <= 0:
            return None

        money_for_shares = share_amount_bought * net_val_to_buy
        fee = money_for_shares * actual_rate * 0.01

        total_money_spent = round(money_for_shares + fee + 0.005, 2)
        fee = round(fee + 0.005, 2)
        cost_per_share = round(total_money_spent / share_amount_bought + 0.0005, 3)

        return {
            "share_amount_bought": int(share_amount_bought),
            "fee": float(fee),
            "total_money": float(total_money_spent),
            "cost_per_share": float(cost_per_share),
            "net_value": float(net_val_to_buy),
        }

    except Exception as e:
        return None
