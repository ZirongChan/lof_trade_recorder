from math import floor

import xalpha as xa

# fundinfo 含全历史净值，重复拉取很慢；按代码缓存
_fund_cache = {}
_nav_map_cache = {}


def clear_fund_cache(code=None):
    if code is None:
        _fund_cache.clear()
        _nav_map_cache.clear()
        return
    code = str(code)
    _fund_cache.pop(code, None)
    _nav_map_cache.pop(code, None)


def get_fund(code, force_refresh=False):
    code = str(code)
    if force_refresh or code not in _fund_cache:
        _fund_cache[code] = xa.fundinfo(code)
        _nav_map_cache.pop(code, None)
    return _fund_cache[code]


def get_nav_map(code):
    """date_str -> netvalue，基于已缓存的 fund.price 建索引。"""
    code = str(code)
    if code not in _nav_map_cache:
        fund = get_fund(code)
        mapping = {}
        for date_val, netvalue in zip(fund.price["date"], fund.price["netvalue"]):
            if hasattr(date_val, "strftime"):
                key = date_val.strftime("%Y-%m-%d")
            else:
                key = str(date_val)[:10]
            mapping[key] = float(netvalue)
        _nav_map_cache[code] = mapping
    return _nav_map_cache[code]


# 返回最新的交易日的信息
def fetch_otc_fund_net_value_latest(code):
    """获取基金数据"""
    try:
        fund = get_fund(code)
        price_info = fund.price.iloc[-1]

        return {
            "net_value": price_info["netvalue"],
            "date": price_info["date"],
        }
    except Exception:
        return None


# 场外基金, over the counter fund, 返回过去n个工作日的信息
def fetch_otc_fund_net_values(code, num_days):
    """获取基金数据"""
    try:
        fund = get_fund(code)
        date_price_infos = {}
        for i in range(num_days):
            price_info = fund.price.iloc[-(i + 1)]
            date = price_info["date"].strftime("%Y-%m-%d")
            date_price_infos[date] = price_info["netvalue"]
        return date_price_infos
    except Exception:
        return None


# 场外基金, over the counter fund, 返回指定时间的信息
def fetch_otc_fund_net_value(code, date):
    try:
        return get_nav_map(code).get(date)
    except Exception:
        return None


# 场内交易的基金的最新价格
def fetch_realtime_market_price(code):
    try:
        stock_info = xa.get_rt(code)
        return stock_info["current"]
    except Exception:
        return None


def fetch_realtime_stock_info(code):
    try:
        return xa.get_rt(code)
    except Exception:
        return None


def get_fund_name(code):
    try:
        fund = get_fund(code)
        return fund.name
    except Exception:
        return None


def _fee_rates(fund):
    actual_rate = fund.rate
    if actual_rate < 0.3:
        declared_rate = actual_rate * 10
    else:
        declared_rate = actual_rate
        actual_rate = declared_rate * 0.1
    return declared_rate, actual_rate


def subscribe_xalpha(code, money_amount, date):
    try:
        fund = get_fund(code)
        declared_rate, _actual_rate = _fee_rates(fund)
        money_to_buy = money_amount * (1 - declared_rate * 0.01 * 0.9)
        res = fund.shengou(value=money_to_buy, date=date)
        print(res)
    except Exception:
        return None


def subscribe(code, money_amount, date):
    try:
        fund = get_fund(code)
        declared_rate, actual_rate = _fee_rates(fund)

        net_val_to_buy = get_nav_map(code).get(date)
        if net_val_to_buy is None:
            return None

        money_to_buy = money_amount * (1 - declared_rate * 0.01)
        share_amount_bought = floor(money_to_buy / net_val_to_buy)
        if share_amount_bought <= 0:
            return None

        money_for_shares = share_amount_bought * net_val_to_buy
        fee = money_for_shares * actual_rate * 0.01

        total_money_spent = round(money_for_shares + fee + 0.005, 2)
        fee = round(fee + 0.005, 2)
        # 申购成本/份固定保留 3 位小数（+0.0005 作四舍五入）
        cost_per_share = round(total_money_spent / share_amount_bought + 0.0005, 3)

        return {
            "share_amount_bought": int(share_amount_bought),
            "fee": float(fee),
            "total_money": float(total_money_spent),
            "cost_per_share": f"{cost_per_share:.3f}",
            "net_value": float(net_val_to_buy),
        }
    except Exception:
        return None


def calc_subscription_fields(code, date_str, buy_money_amount):
    """
    按申购金额计算净值/到账/成本。
    金额≤0：清空申购字段，仍尽量取净值；不改买入/卖出。
    buy_shares 仅在申购成功时给出建议值（到账份额），由 UI 决定是否写入。
    """
    result = {
        "nav": None,
        "shares": None,
        "cost": None,
        "buy_shares": None,
        "error": None,
    }
    if not date_str:
        result["error"] = "empty date"
        return result

    if buy_money_amount <= 0:
        result["nav"] = fetch_otc_fund_net_value(code, date_str)
        result["shares"] = "-"
        result["cost"] = "-"
        return result

    buy_res = subscribe(code, buy_money_amount, date_str)
    if buy_res is None:
        nav = fetch_otc_fund_net_value(code, date_str)
        result["nav"] = nav
        if nav is None:
            result["error"] = f"{date_str} 无净值数据 for {code}"
        else:
            result["error"] = f"subscribe failed for {code} @ {date_str}"
        return result

    result["nav"] = buy_res.get("net_value")
    shares = buy_res["share_amount_bought"]
    result["shares"] = shares
    result["cost"] = buy_res["cost_per_share"]
    result["buy_shares"] = shares
    return result
