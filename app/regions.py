from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TargetMarket:
    code: str
    name: str
    english_name: str
    lat: float
    lon: float
    area: str
    shared_scopes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["shared_scopes"] = list(self.shared_scopes)
        return data


# 预研阶段使用的 21 个目标国家/地区。正式实施时允许业务方调整名单；
# 所有业务代码只依赖 code，不依赖列表顺序。
TARGET_MARKETS: tuple[TargetMarket, ...] = (
    TargetMarket("DE", "德国", "Germany", 51.1, 10.4, "欧洲", ("EU",)),
    TargetMarket("FR", "法国", "France", 46.2, 2.2, "欧洲", ("EU",)),
    TargetMarket("IT", "意大利", "Italy", 42.8, 12.5, "欧洲", ("EU",)),
    TargetMarket("ES", "西班牙", "Spain", 40.4, -3.7, "欧洲", ("EU",)),
    TargetMarket("NL", "荷兰", "Netherlands", 52.1, 5.3, "欧洲", ("EU",)),
    TargetMarket("BE", "比利时", "Belgium", 50.8, 4.7, "欧洲", ("EU",)),
    TargetMarket("SE", "瑞典", "Sweden", 62.0, 15.0, "欧洲", ("EU",)),
    TargetMarket("DK", "丹麦", "Denmark", 56.0, 9.5, "欧洲", ("EU",)),
    TargetMarket("FI", "芬兰", "Finland", 64.0, 26.0, "欧洲", ("EU",)),
    TargetMarket("AT", "奥地利", "Austria", 47.5, 14.5, "欧洲", ("EU",)),
    TargetMarket("IE", "爱尔兰", "Ireland", 53.4, -8.0, "欧洲", ("EU",)),
    TargetMarket("NO", "挪威", "Norway", 61.0, 8.0, "欧洲"),
    TargetMarket("CH", "瑞士", "Switzerland", 46.8, 8.2, "欧洲"),
    TargetMarket("GB", "英国", "United Kingdom", 54.5, -3.0, "欧洲"),
    TargetMarket("US", "美国", "United States", 38.0, -97.0, "北美"),
    TargetMarket("CA", "加拿大", "Canada", 56.1, -106.3, "北美"),
    TargetMarket("JP", "日本", "Japan", 36.2, 138.2, "亚太"),
    TargetMarket("KR", "韩国", "South Korea", 36.3, 127.8, "亚太"),
    TargetMarket("AU", "澳大利亚", "Australia", -25.3, 133.8, "亚太"),
    TargetMarket("NZ", "新西兰", "New Zealand", -41.3, 174.8, "亚太"),
    TargetMarket("SG", "新加坡", "Singapore", 1.35, 103.82, "亚太"),
)

TARGET_MARKET_BY_CODE = {item.code: item for item in TARGET_MARKETS}

# 兼容已有演示/历史数据中的代码。
REGION_ALIASES = {
    "UK": "GB",
    "KOREA": "KR",
    "SOUTH_KOREA": "KR",
}

REGION_GROUPS: dict[str, dict[str, Any]] = {
    "EU": {"name": "欧盟", "lat": 50.85, "lon": 4.35, "area": "区域"},
}

REGION_META: dict[str, dict[str, Any]] = {
    **{item.code: {
        "name": item.name,
        "english_name": item.english_name,
        "lat": item.lat,
        "lon": item.lon,
        "area": item.area,
        "shared_scopes": list(item.shared_scopes),
        "target_market": True,
    } for item in TARGET_MARKETS},
    **{key: {**value, "target_market": False} for key, value in REGION_GROUPS.items()},
}


def canonical_region_code(code: str) -> str:
    value = str(code or "").strip().upper()
    return REGION_ALIASES.get(value, value)


def target_market(code: str) -> TargetMarket | None:
    return TARGET_MARKET_BY_CODE.get(canonical_region_code(code))


def knowledge_scope_codes(code: str) -> tuple[str, ...]:
    """Return country scope plus shared regulatory scopes used for applicability.

    Example: DE -> ("DE", "EU").  GB -> ("GB",).
    """
    canonical = canonical_region_code(code)
    market = TARGET_MARKET_BY_CODE.get(canonical)
    if not market:
        return (canonical,) if canonical else ()
    return (market.code, *market.shared_scopes)


def target_market_catalog() -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for market in TARGET_MARKETS:
        grouped.setdefault(market.area, []).append(market.to_dict())
    return {
        "count": len(TARGET_MARKETS),
        "status": "预研暂定",
        "note": "当前按预研阶段暂定21个目标国家/地区推进，正式实施时由业务方确认后可调整。",
        "markets": [item.to_dict() for item in TARGET_MARKETS],
        "groups": grouped,
        "shared_regions": [
            {"code": code, **meta}
            for code, meta in REGION_GROUPS.items()
        ],
    }
