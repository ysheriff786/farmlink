"""
AI-Powered Demand Forecasting Module for FarmLink.

Data source: Real historical FarmLink order data (order_items + orders).
No hard-coded demand numbers, no fake historical data.

Pipeline:
  Orders -> Data cleaning -> Daily demand aggregation -> Feature engineering
  -> Baseline model -> ML model -> 7-day prediction -> Dashboard

When insufficient data exists, the system operates in clearly labeled
demo/fallback mode rather than pretending predictions are trained on
real data.
"""

import hashlib
import threading
import time
from datetime import date, datetime, timedelta
from math import sqrt

import numpy as np
import pandas as pd
from sqlalchemy import text

try:
    from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FORECAST_HORIZON = 7
MIN_HISTORY_DAYS = 7
CACHE_TTL = 300
DEMO_THRESHOLD_DAYS = 14

DEMAND_STATUSES = (
    "Placed", "Confirmed", "Out for Delivery", "Delivered",
    "Pending Payment",
)


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_cache = {}
_cache_lock = threading.Lock()


def _cache_key(*args):
    raw = "|".join(str(a) for a in args)
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry["ts"] < CACHE_TTL:
            return entry["data"]
    return None


def _set_cached(key, data):
    with _cache_lock:
        _cache[key] = {"data": data, "ts": time.time()}


def clear_cache():
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------------

def _get_session():
    """Get the SQLAlchemy session from the Flask app context."""
    from flask import current_app
    return current_app.extensions["sqlalchemy"].session


# ---------------------------------------------------------------------------
# Data pipeline
# ---------------------------------------------------------------------------

def load_order_data(farmer_id=None, days_back=90):
    """Load real order data from the database.

    Returns a DataFrame with columns:
        date, product_name, quantity, order_id, farmer_id, price
    """
    session = _get_session()
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()

    status_placeholders = ", ".join(f":s{i}" for i in range(len(DEMAND_STATUSES)))
    farmer_filter = ""
    params = {"cutoff": cutoff}
    for i, s in enumerate(DEMAND_STATUSES):
        params[f"s{i}"] = s

    if farmer_id:
        farmer_filter = "AND oi.farmer_id = :fid"
        params["fid"] = farmer_id

    sql = f"""
        SELECT
            date(o.created_at) AS date,
            oi.product_name,
            oi.quantity,
            oi.order_id,
            oi.farmer_id,
            oi.price
        FROM order_items oi
        JOIN orders o ON oi.order_id = o.id
        WHERE o.status IN ({status_placeholders})
          AND o.created_at >= :cutoff
          AND oi.quantity > 0
          {farmer_filter}
        ORDER BY o.created_at
    """
    rows = session.execute(text(sql), params).mappings().fetchall()
    if not rows:
        return pd.DataFrame(columns=[
            "date", "product_name", "quantity",
            "order_id", "farmer_id", "price",
        ])
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["quantity"] = df["quantity"].astype(float)
    return df


def aggregate_daily_demand(df):
    """Aggregate order data into daily demand per product."""
    if df.empty:
        return df
    agg = df.groupby(["date", "product_name"])["quantity"].sum().reset_index()
    agg.columns = ["date", "product_name", "daily_qty"]
    agg = agg.sort_values(["product_name", "date"]).reset_index(drop=True)
    return agg


def fill_missing_dates(agg_df, products=None):
    """Fill gaps in the date range so every product has a row per day."""
    if agg_df.empty:
        return agg_df

    all_dates = pd.date_range(
        start=agg_df["date"].min(),
        end=agg_df["date"].max(),
        freq="D",
    ).date

    if products is None:
        products = agg_df["product_name"].unique()

    rows = []
    for prod in products:
        prod_data = agg_df[agg_df["product_name"] == prod].set_index("date")
        for d in all_dates:
            qty = prod_data.loc[d, "daily_qty"] if d in prod_data.index else 0.0
            rows.append({"date": d, "product_name": prod, "daily_qty": float(qty)})

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def build_features(df):
    """Create ML features from daily demand time series."""
    df = df.sort_values("date").copy()
    df["date"] = pd.to_datetime(df["date"])
    df["day_of_week"] = df["date"].dt.dayofweek
    df["day_of_month"] = df["date"].dt.day
    df["month"] = df["date"].dt.month

    df["prev_day_qty"] = df["daily_qty"].shift(1)
    df["rolling_3"] = df["daily_qty"].shift(1).rolling(window=3, min_periods=1).mean()
    df["rolling_7"] = df["daily_qty"].shift(1).rolling(window=7, min_periods=1).mean()
    df["rolling_14"] = df["daily_qty"].shift(1).rolling(window=14, min_periods=1).mean()
    df["rolling_30"] = df["daily_qty"].shift(1).rolling(window=30, min_periods=1).mean()
    df["prev_7d_total"] = df["daily_qty"].shift(1).rolling(window=7, min_periods=1).sum()
    df["prev_14d_total"] = df["daily_qty"].shift(1).rolling(window=14, min_periods=1).sum()
    df["prev_30d_total"] = df["daily_qty"].shift(1).rolling(window=30, min_periods=1).sum()

    df = df.fillna(0)
    return df


FEATURE_COLS = [
    "day_of_week", "day_of_month", "month",
    "prev_day_qty", "rolling_3", "rolling_7", "rolling_14", "rolling_30",
    "prev_7d_total", "prev_14d_total", "prev_30d_total",
]


# ---------------------------------------------------------------------------
# Baseline models
# ---------------------------------------------------------------------------

def baseline_moving_average(daily_series, window=7):
    """Simple moving average baseline."""
    if len(daily_series) == 0:
        return 0.0
    w = min(window, len(daily_series))
    return float(np.mean(daily_series[-w:]))


def baseline_exponential_moving_average(daily_series, alpha=0.3):
    """Exponential moving average baseline."""
    if len(daily_series) == 0:
        return 0.0
    ema = daily_series[0]
    for val in daily_series[1:]:
        ema = alpha * val + (1 - alpha) * ema
    return float(ema)


# ---------------------------------------------------------------------------
# ML model
# ---------------------------------------------------------------------------

def train_ml_model(X, y):
    """Train a Gradient Boosting or Random Forest regressor."""
    if not HAS_SKLEARN or len(X) < MIN_HISTORY_DAYS:
        return None, {}

    X_arr = np.array(X, dtype=float)
    y_arr = np.array(y, dtype=float)

    if len(X_arr) < 10:
        model = RandomForestRegressor(
            n_estimators=50, max_depth=5, random_state=42, min_samples_split=2
        )
    else:
        model = GradientBoostingRegressor(
            n_estimators=100, max_depth=4, learning_rate=0.1, random_state=42
        )

    model.fit(X_arr, y_arr)

    y_pred = model.predict(X_arr)
    mae = float(mean_absolute_error(y_arr, y_pred))
    rmse = float(sqrt(mean_squared_error(y_arr, y_pred)))
    r2 = float(r2_score(y_arr, y_pred)) if len(y_arr) > 1 else 0.0

    metrics = {
        "mae": round(mae, 2),
        "rmse": round(rmse, 2),
        "r2": round(r2, 4),
        "train_samples": len(X_arr),
    }
    return model, metrics


# ---------------------------------------------------------------------------
# Forecast generation
# ---------------------------------------------------------------------------

def classify_trend(historical_avg, forecast_avg):
    """Classify demand trend based on recent vs forecast averages."""
    if historical_avg == 0:
        if forecast_avg > 0:
            return "Increasing"
        return "Stable"
    change_pct = (forecast_avg - historical_avg) / historical_avg
    if change_pct > 0.10:
        return "Increasing"
    elif change_pct < -0.10:
        return "Decreasing"
    return "Stable"


def compute_confidence(model, metrics, data_days):
    """Compute a confidence score (0-100) based on data quality and model performance."""
    if data_days < MIN_HISTORY_DAYS:
        return None, "Confidence unavailable -- insufficient historical data."

    score = 0.0

    data_score = min(data_days / 60.0, 1.0) * 40
    score += data_score

    if metrics and "r2" in metrics:
        r2_clamped = max(0, min(metrics["r2"], 1.0))
        score += r2_clamped * 40
    else:
        score += 20

    if model is not None:
        score += 20

    score = max(0, min(100, round(score)))

    if score >= 70:
        label = f"High confidence ({score}%)"
    elif score >= 40:
        label = f"Moderate confidence ({score}%)"
    else:
        label = f"Low confidence ({score}%) -- limited historical data."

    return score, label


def generate_forecast(product_name, forecast_days=FORECAST_HORIZON, farmer_id=None):
    """Generate a demand forecast for a single product."""
    ck = _cache_key("forecast", product_name, forecast_days, farmer_id)
    cached = _get_cached(ck)
    if cached:
        return cached

    raw_df = load_order_data(farmer_id=farmer_id, days_back=90)
    agg = aggregate_daily_demand(raw_df)
    prod_agg = agg[agg["product_name"] == product_name].copy() if not agg.empty else pd.DataFrame()

    today = date.today()
    data_days = 0
    demo_mode = False

    if prod_agg.empty or len(prod_agg) < 2:
        result = _empty_forecast(product_name, today)
        _set_cached(ck, result)
        return result

    filled = fill_missing_dates(prod_agg)
    data_days = filled["date"].nunique()
    demo_mode = data_days < DEMO_THRESHOLD_DAYS

    daily_qtys = filled["daily_qty"].values
    last_7d = float(np.sum(daily_qtys[-7:])) if len(daily_qtys) >= 7 else float(np.sum(daily_qtys))
    last_30d = float(np.sum(daily_qtys[-30:])) if len(daily_qtys) >= 30 else float(np.sum(daily_qtys))
    daily_avg = float(np.mean(daily_qtys))

    ma_pred = baseline_moving_average(daily_qtys, window=7)
    ema_pred = baseline_exponential_moving_average(daily_qtys)

    featured = build_features(filled)
    X = featured[FEATURE_COLS].values
    y = featured["daily_qty"].values

    model, metrics = train_ml_model(X, y)

    forecast_rows = []
    last_known = filled.copy()
    last_known["date"] = pd.to_datetime(last_known["date"])

    for i in range(1, forecast_days + 1):
        future_date = today + timedelta(days=i)
        future_dt = pd.Timestamp(future_date)

        feat_row = _build_future_features(last_known, future_dt, i)
        feat_arr = np.array([[feat_row[col] for col in FEATURE_COLS]], dtype=float)

        if model is not None:
            pred = max(0.0, float(model.predict(feat_arr)[0]))
        else:
            pred = max(0.0, 0.6 * ema_pred + 0.4 * ma_pred)

        forecast_rows.append({
            "date": future_date.isoformat(),
            "qty": round(pred, 1),
        })

        new_row = pd.DataFrame([{
            "date": future_dt,
            "product_name": product_name,
            "daily_qty": pred,
        }])
        last_known = pd.concat([last_known, new_row], ignore_index=True)

    total_forecast = round(sum(r["qty"] for r in forecast_rows), 1)
    forecast_avg = total_forecast / forecast_days if forecast_days else 0

    trend = classify_trend(daily_avg, forecast_avg)
    conf_score, conf_label = compute_confidence(model, metrics, data_days)

    if model is not None:
        model_name = type(model).__name__
    else:
        model_name = "Baseline (EMA + Moving Average)"

    result = {
        "product": product_name,
        "historical_demand": {
            "last_7d": round(last_7d, 1),
            "last_30d": round(last_30d, 1),
            "daily_avg": round(daily_avg, 1),
            "data_days": data_days,
        },
        "forecast": forecast_rows,
        "total_forecast": total_forecast,
        "trend": trend,
        "confidence": {
            "score": conf_score,
            "label": conf_label,
        },
        "model_used": model_name,
        "model_metrics": metrics,
        "demo_mode": demo_mode,
        "generated_at": datetime.now().isoformat(),
        "data_source": (
            "Real FarmLink order data" if not demo_mode
            else "Limited FarmLink order data (demo mode)"
        ),
    }

    _set_cached(ck, result)
    return result


def _build_future_features(df, future_dt, day_offset):
    """Build feature vector for a future date using the growing history."""
    recent = df["daily_qty"].values
    if len(recent) > 30:
        recent = recent[-30:]

    prev_day = float(recent[-1]) if len(recent) > 0 else 0.0
    r3 = float(np.mean(recent[-3:])) if len(recent) >= 3 else float(np.mean(recent))
    r7 = float(np.mean(recent[-7:])) if len(recent) >= 7 else float(np.mean(recent))
    r14 = float(np.mean(recent[-14:])) if len(recent) >= 14 else float(np.mean(recent))
    r30 = float(np.mean(recent[-30:])) if len(recent) >= 30 else float(np.mean(recent))
    t7 = float(np.sum(recent[-7:])) if len(recent) >= 7 else float(np.sum(recent))
    t14 = float(np.sum(recent[-14:])) if len(recent) >= 14 else float(np.sum(recent))
    t30 = float(np.sum(recent[-30:])) if len(recent) >= 30 else float(np.sum(recent))

    return {
        "day_of_week": future_dt.dayofweek,
        "day_of_month": future_dt.day,
        "month": future_dt.month,
        "prev_day_qty": prev_day,
        "rolling_3": r3,
        "rolling_7": r7,
        "rolling_14": r14,
        "rolling_30": r30,
        "prev_7d_total": t7,
        "prev_14d_total": t14,
        "prev_30d_total": t30,
    }


def _empty_forecast(product_name, today):
    return {
        "product": product_name,
        "historical_demand": {
            "last_7d": 0, "last_30d": 0, "daily_avg": 0, "data_days": 0,
        },
        "forecast": [{"date": (today + timedelta(days=i)).isoformat(), "qty": 0}
                     for i in range(1, FORECAST_HORIZON + 1)],
        "total_forecast": 0,
        "trend": "Stable",
        "confidence": {"score": None, "label": "Confidence unavailable -- no historical data."},
        "model_used": "N/A",
        "model_metrics": {},
        "demo_mode": True,
        "generated_at": datetime.now().isoformat(),
        "data_source": "No order data available for this product.",
    }


# ---------------------------------------------------------------------------
# Aggregate forecast (all products)
# ---------------------------------------------------------------------------

def generate_all_forecasts(farmer_id=None, forecast_days=FORECAST_HORIZON):
    """Generate forecasts for all products that have order data."""
    ck = _cache_key("all_forecasts", farmer_id, forecast_days)
    cached = _get_cached(ck)
    if cached:
        return cached

    raw_df = load_order_data(farmer_id=farmer_id, days_back=90)
    if raw_df.empty:
        return []

    products = raw_df["product_name"].unique()
    results = []
    for prod in products:
        fc = generate_forecast(prod, forecast_days=forecast_days, farmer_id=farmer_id)
        results.append(fc)

    results.sort(key=lambda x: x["total_forecast"], reverse=True)
    _set_cached(ck, results)
    return results


def get_demand_analytics():
    """Get aggregate demand analytics for the admin dashboard."""
    ck = _cache_key("demand_analytics")
    cached = _get_cached(ck)
    if cached:
        return cached

    raw_df = load_order_data(days_back=90)
    if raw_df.empty:
        result = {
            "products": [],
            "total_demand_7d": 0,
            "total_demand_30d": 0,
            "data_available": False,
            "demo_mode": True,
        }
        _set_cached(ck, result)
        return result

    agg = aggregate_daily_demand(raw_df)
    filled = fill_missing_dates(agg)

    products_info = []
    for prod_name in filled["product_name"].unique():
        prod_data = filled[filled["product_name"] == prod_name].sort_values("date")
        daily_qtys = prod_data["daily_qty"].values

        d7 = float(np.sum(daily_qtys[-7:])) if len(daily_qtys) >= 7 else float(np.sum(daily_qtys))
        d30 = float(np.sum(daily_qtys[-30:])) if len(daily_qtys) >= 30 else float(np.sum(daily_qtys))
        daily_avg = float(np.mean(daily_qtys))
        data_days = len(prod_data)

        fc = generate_forecast(prod_name)

        products_info.append({
            "name": prod_name,
            "demand_7d": round(d7, 1),
            "demand_30d": round(d30, 1),
            "daily_avg": round(daily_avg, 1),
            "forecast_7d": fc["total_forecast"],
            "trend": fc["trend"],
            "data_days": data_days,
            "demo_mode": fc["demo_mode"],
        })

    products_info.sort(key=lambda x: x["forecast_7d"], reverse=True)

    total_7d = round(sum(p["demand_7d"] for p in products_info), 1)
    total_30d = round(sum(p["demand_30d"] for p in products_info), 1)

    result = {
        "products": products_info,
        "total_demand_7d": total_7d,
        "total_demand_30d": total_30d,
        "total_products": len(products_info),
        "data_available": True,
        "demo_mode": any(p["demo_mode"] for p in products_info),
    }
    _set_cached(ck, result)
    return result


def get_farmer_forecasts(farmer_id):
    """Get demand forecasts for products sold by a specific farmer."""
    return generate_all_forecasts(farmer_id=farmer_id)


def get_fpo_demand_insight(fpo_id):
    """Get demand vs supply insight for FPO dashboard."""
    ck = _cache_key("fpo_insight", fpo_id)
    cached = _get_cached(ck)
    if cached:
        return cached

    session = _get_session()

    supplies = session.execute(
        text(
            "SELECT p.id, p.name, p.unit, p.stock, p.is_aggregate "
            "FROM products p WHERE p.farmer_id=:fid AND p.is_aggregate=1"
        ),
        {"fid": fpo_id},
    ).mappings().fetchall()

    insights = []
    for s in supplies:
        supply_qty = float(s["stock"])
        fc = generate_forecast(s["name"])
        forecast_qty = fc["total_forecast"]

        gap = round(forecast_qty - supply_qty, 1) if forecast_qty > supply_qty else 0
        surplus = round(supply_qty - forecast_qty, 1) if supply_qty > forecast_qty else 0

        insights.append({
            "product": s["name"],
            "unit": s["unit"],
            "current_supply": supply_qty,
            "forecast_demand": forecast_qty,
            "gap": gap,
            "surplus": surplus,
            "trend": fc["trend"],
            "recommendation": _fpo_recommendation(gap, surplus, fc["trend"]),
            "demo_mode": fc["demo_mode"],
        })

    result = {"insights": insights, "generated_at": datetime.now().isoformat()}
    _set_cached(ck, result)
    return result


def _fpo_recommendation(gap, surplus, trend):
    """Generate actionable recommendation for FPO."""
    if gap > 0:
        return (
            f"Additional {gap:.0f} units may be required to meet forecast demand. "
            f"Trend is {trend.lower()}."
        )
    elif surplus > 0:
        return (
            f"Current supply exceeds forecast by {surplus:.0f} units. "
            f"Consider expanding market reach."
        )
    else:
        return f"Supply is well matched to forecast demand. Trend is {trend.lower()}."


# ---------------------------------------------------------------------------
# Bulk buyer demand insight
# ---------------------------------------------------------------------------

def get_bulk_buyer_insight(product_name):
    """Get demand insight for a product that a bulk buyer is interested in."""
    fc = generate_forecast(product_name)
    return {
        "product": product_name,
        "forecast_7d": fc["total_forecast"],
        "trend": fc["trend"],
        "historical_7d": fc["historical_demand"]["last_7d"],
        "daily_avg": fc["historical_demand"]["daily_avg"],
        "confidence": fc["confidence"],
        "demo_mode": fc["demo_mode"],
    }
