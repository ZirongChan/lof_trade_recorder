from math import floor

import xalpha as xa

# fundinfo 含全历史净值，重复拉取很慢；按代码缓存
_fund_cache = {}
_nav_map_cache = {}
_redeem_tiers_cache = {}

# 无费率数据时的回退：仅「满7日剩余」一档（与旧列语义一致）
DEFAULT_REDEEM_TIERS = (
    {
        "label": "满7日剩余",
        "rate_pct": None,
        "day_lo": 7,
        "day_hi": None,
    },
)


def clear_fund_cache(code=None):
    if code is None:
        _fund_cache.clear()
        _nav_map_cache.clear()
        _redeem_tiers_cache.clear()
        return
    code = str(code)
    _fund_cache.pop(code, None)
    _nav_map_cache.pop(code, None)
    _redeem_tiers_cache.pop(code, None)


def get_fund(code, force_refresh=False):
    code = str(code)
    if force_refresh or code not in _fund_cache:
        _fund_cache[code] = xa.fundinfo(code)
        _nav_map_cache.pop(code, None)
        _redeem_tiers_cache.pop(code, None)
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


def get_subscribe_actual_rate(code):
    """标题展示用：实际佣金费率（%），如 0.12；失败返回 None。"""
    try:
        if not code or not str(code).isdigit() or len(str(code)) != 6:
            return None
        _declared, actual = _fee_rates(get_fund(str(code)))
        return float(actual)
    except Exception:
        return None


def subscribe_xalpha(code, money_amount, date):
    try:
        fund = get_fund(code)
        declared_rate, _actual_rate = _fee_rates(fund)
        # 份额口径：按公告费率外扣后的净额
        money_to_buy = money_amount / (1 + declared_rate * 0.01)
        res = fund.shengou(value=money_to_buy, date=date)
        print(res)
    except Exception:
        return None


def subscribe(code, money_amount, date):
    """
    对齐常见券商确认单（如华宝）：
      到账份额   = floor( 申购金额 / (1 + 公告费率) / 净值 )   ← 外扣用公告档
      成交金额   = 到账份额 × 净值
      佣金       = 成交金额 × 实际费率（多为公告档的 1 折）
      成本/份    = (成交金额 + 佣金) / 到账份额
    """
    try:
        fund = get_fund(code)
        declared_rate, actual_rate = _fee_rates(fund)

        net_val_to_buy = get_nav_map(code).get(date)
        if net_val_to_buy is None:
            return None

        declared = declared_rate * 0.01
        if declared <= -1:
            return None
        net_money = money_amount / (1 + declared)
        share_amount_bought = floor(net_money / net_val_to_buy)
        if share_amount_bought <= 0:
            return None

        # 成交金额 = 份额 × 净值；佣金按折扣后实际费率
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
            "deal_amount": round(money_for_shares + 1e-9, 2),
        }
    except Exception:
        return None


def _parse_rate_pct(text):
    try:
        return float(str(text).strip().replace("%", ""))
    except (TypeError, ValueError):
        return None


def _tier_short_label(day_lo, day_hi, rate_pct):
    """由持有天下界生成短标题，并附费率。"""
    day_lo = int(day_lo or 0)
    if day_lo <= 0:
        if day_hi is not None:
            base = f"未满{int(day_hi)}日"
        else:
            base = "持有中"
    elif day_lo % 365 == 0 and day_lo >= 365:
        base = f"满{day_lo // 365}年"
    elif day_lo == 7:
        base = "满7日"
    else:
        base = f"满{day_lo}日"

    if rate_pct is None:
        return base if base != "满7日" else "满7日剩余"
    return f"{base}（{float(rate_pct):.2f}%）"


def _tiers_from_feeinfo(feeinfo, segment):
    """feeinfo 为 [描述, 费率%, ...]；segment 如 [[0,7],[7,365],[365,730],[730]]。"""
    if not feeinfo or len(feeinfo) < 2 or len(feeinfo) % 2 != 0:
        return None
    n = len(feeinfo) // 2
    tiers = []
    for i in range(n):
        rate_pct = _parse_rate_pct(feeinfo[2 * i + 1])
        day_lo, day_hi = 0, None
        if segment and i < len(segment):
            seg = segment[i]
            if isinstance(seg, (list, tuple)) and len(seg) >= 1:
                day_lo = int(seg[0])
                day_hi = int(seg[1]) if len(seg) >= 2 else None
        label = _tier_short_label(day_lo, day_hi, rate_pct)
        tiers.append(
            {
                "label": label,
                "rate_pct": rate_pct,
                "day_lo": day_lo,
                "day_hi": day_hi,
            }
        )
    return tiers or None


def get_redeem_tiers(code=None):
    """
    赎回费率持有期限分段。
    返回 [{label, rate_pct, day_lo, day_hi}, ...]；day_hi 为 None 表示无上界。
    """
    if not code or not str(code).isdigit() or len(str(code)) != 6:
        return [dict(t) for t in DEFAULT_REDEEM_TIERS]

    code = str(code)
    if code in _redeem_tiers_cache:
        return [dict(t) for t in _redeem_tiers_cache[code]]

    try:
        fund = get_fund(code)
        feeinfo = getattr(fund, "feeinfo", None) or []
        segment = getattr(fund, "segment", None)
        tiers = _tiers_from_feeinfo(feeinfo, segment)
        if not tiers:
            tiers = [dict(t) for t in DEFAULT_REDEEM_TIERS]
        _redeem_tiers_cache[code] = [dict(t) for t in tiers]
        return [dict(t) for t in tiers]
    except Exception:
        return [dict(t) for t in DEFAULT_REDEEM_TIERS]


def calc_subscription_fields(code, date_str, buy_money_amount):
    """
    按申购金额计算净值/到账/成本。
    金额≤0：清空申购字段，仍尽量取净值；不改买入/卖出/赎回。
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
