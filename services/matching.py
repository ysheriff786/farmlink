import re

MATCH_STOPWORDS = frozenset([
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "this", "that", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "may", "might", "can",
    "shall", "not", "no", "nor", "so", "if", "then", "than", "too",
    "very", "just", "about", "above", "after", "again", "all", "also",
    "any", "because", "before", "between", "both", "each", "few", "more",
    "most", "other", "some", "such", "into", "only", "own", "same",
    "through", "during", "out", "up", "down",
])


def match_tokens(text):
    """Extract meaningful tokens from text for matching."""
    if not text:
        return set()
    tokens = set(re.findall(r"[a-z0-9]{3,}", text.lower()))
    return tokens - MATCH_STOPWORDS


def calculate_match_score(requirement, supply):
    """Calculate a match score between a bulk requirement and FPO supply."""
    score = 0
    reasons = []
    breakdown = {}

    req_tokens = match_tokens(requirement.get("title", "") + " " + requirement.get("description", ""))
    supply_tokens = match_tokens(supply.get("name", "") + " " + supply.get("description", ""))

    token_overlap = len(req_tokens & supply_tokens)
    token_union = len(req_tokens | supply_tokens)
    token_score = (token_overlap / token_union * 40) if token_union > 0 else 0
    breakdown["text_match"] = round(token_score, 1)
    if token_overlap > 0:
        reasons.append(f"Matching keywords: {', '.join(sorted(req_tokens & supply_tokens))}")

    if requirement.get("category", "").lower() == supply.get("category", "").lower():
        category_score = 20
        reasons.append("Same category")
    else:
        category_score = 0
    breakdown["category_match"] = category_score

    if requirement.get("unit", "").lower() == supply.get("unit", "").lower():
        unit_score = 15
        reasons.append("Same unit")
    else:
        unit_score = 0
    breakdown["unit_match"] = unit_score

    req_qty = requirement.get("quantity", 0)
    supply_stock = supply.get("stock", 0) or supply.get("on_hand", 0)
    if supply_stock >= req_qty:
        qty_score = 15
        reasons.append(f"Sufficient stock ({supply_stock} >= {req_qty})")
    elif supply_stock >= req_qty * 0.5:
        qty_score = 8
        reasons.append(f"Partial stock ({supply_stock}/{req_qty})")
    else:
        qty_score = 0
    breakdown["quantity_match"] = qty_score

    target = requirement.get("target_price")
    price = supply.get("price", 0)
    if target and price:
        if price <= target:
            price_score = 10
            reasons.append(f"Within target price (\u20b9{price} <= \u20b9{target})")
        elif price <= target * 1.1:
            price_score = 5
            reasons.append(f"Slightly above target (\u20b9{price} vs \u20b9{target})")
        else:
            price_score = 0
    else:
        price_score = 5
    breakdown["price_match"] = price_score

    score = round(token_score + category_score + unit_score + qty_score + price_score, 1)

    return {
        "score": min(score, 100),
        "breakdown": breakdown,
        "reasons": reasons,
    }


def find_matching_supplies(db, requirement):
    """Find and score all matching FPO supplies for a requirement."""
    from sqlalchemy import text
    supplies = db.execute(
        text(
            "SELECT p.*, u.name as fpo_name, u.id as fpo_id "
            "FROM products p "
            "JOIN users u ON u.id = p.farmer_id "
            "WHERE u.role='fpo' AND p.stock > 0"
        )
    ).mappings().fetchall()

    matches = []
    for supply in supplies:
        score_info = calculate_match_score(requirement, dict(supply))
        if score_info["score"] > 0:
            supply_dict = dict(supply)
            supply_dict["match_score"] = score_info["score"]
            supply_dict["match_reasons"] = score_info["reasons"]
            supply_dict["match_breakdown"] = score_info["breakdown"]
            matches.append(supply_dict)

    matches.sort(key=lambda x: x["match_score"], reverse=True)
    return matches


def find_best_supplier(db, requirement):
    """Find the single best matching supplier for a requirement."""
    matches = find_matching_supplies(db, requirement)
    if not matches:
        return None, None
    best = matches[0]
    return best, {
        "score": best["match_score"],
        "reasons": best["match_reasons"],
        "breakdown": best["match_breakdown"],
    }


def enhance_matching_with_distance(db, matches, buyer_lat=None, buyer_lng=None):
    """Enhance match scores with distance component."""
    if buyer_lat is None or buyer_lng is None:
        return matches

    from sqlalchemy import text

    from services.location import haversine

    for match in matches:
        profile = db.execute(
            text("SELECT latitude, longitude FROM fpo_profiles WHERE user_id=:uid"),
            {"uid": match["fpo_id"]},
        ).mappings().first()

        if profile and profile["latitude"] and profile["longitude"]:
            dist = haversine(buyer_lat, buyer_lng,
                             profile["latitude"], profile["longitude"])
            match["distance_km"] = round(dist, 2)
            if dist < 50:
                distance_bonus = 10
            elif dist < 100:
                distance_bonus = 5
            elif dist < 200:
                distance_bonus = 2
            else:
                distance_bonus = 0
            match["distance_bonus"] = distance_bonus
            match["adjusted_score"] = min(match["match_score"] + distance_bonus, 100)
        else:
            match["distance_km"] = None
            match["distance_bonus"] = 0
            match["adjusted_score"] = match["match_score"]

    matches.sort(key=lambda x: x.get("adjusted_score", x["match_score"]), reverse=True)
    return matches
