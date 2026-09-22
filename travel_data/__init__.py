"""可替换数据供应边界；不实现搜索、评分或真实库存。"""

from .providers import (
    DataProvenance,
    DestinationProfile,
    DestinationProvider,
    FlightProvider,
    FlightQuery,
    FlightQuote,
    FlightSearchResult,
    PreferenceMatch,
    ProviderError,
    SyntheticDestinationProvider,
    SyntheticFlightProvider,
)

__all__ = [
    "DataProvenance", "DestinationProfile", "DestinationProvider", "FlightProvider",
    "FlightQuery", "FlightQuote", "FlightSearchResult", "PreferenceMatch",
    "ProviderError", "SyntheticDestinationProvider", "SyntheticFlightProvider",
]
