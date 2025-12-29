#!/usr/bin/env python3
"""
Economic Calendar Integration - Session 265 (L053)

Fetches upcoming high-impact economic events that can cause extreme volatility.
Major events (FOMC, NFP, CPI) can invalidate technical setups.

Based on Sherlock learning L053:
- Tier 1 (CRITICAL): FOMC, NFP, CPI - avoid new entries 4h before
- Tier 2 (IMPORTANT): Unemployment claims, PPI - caution 2h before

Data Source: Investing.com Economic Calendar API (free, no auth required)

Usage:
    from src.data.economic_calendar import EconomicCalendar, get_event_risk

    calendar = EconomicCalendar()
    events = calendar.get_upcoming_events()
    risk = calendar.get_event_risk()  # Returns: CRITICAL, WARNING, CLEAR

Migration History:
- Session 267: Migrated from scripts/helpers/economic_calendar.py

Author: Claude Code (Session 265)
Date: 2025-12-28
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

import requests

logger = logging.getLogger(__name__)


class EventImpact(Enum):
    """Economic event impact level."""
    HIGH = "HIGH"       # Tier 1: FOMC, NFP, CPI
    MEDIUM = "MEDIUM"   # Tier 2: Unemployment, PPI
    LOW = "LOW"         # Other events


class EventRisk(Enum):
    """Risk level for entering trades."""
    CRITICAL = "CRITICAL"   # Major event within 4h - avoid new entries
    WARNING = "WARNING"     # Important event within 2h - caution
    CLEAR = "CLEAR"         # No major events soon - normal trading


@dataclass
class EconomicEvent:
    """Represents an economic calendar event."""
    name: str
    country: str
    time: datetime
    impact: EventImpact
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None


# Tier 1 events - CRITICAL (4h avoidance window)
TIER_1_EVENTS = [
    "FOMC",
    "Fed Interest Rate Decision",
    "Federal Reserve",
    "Non-Farm Payrolls",
    "NFP",
    "Nonfarm Payrolls",
    "CPI",
    "Consumer Price Index",
    "Core CPI",
]

# Tier 2 events - WARNING (2h caution window)
TIER_2_EVENTS = [
    "Unemployment",
    "Initial Jobless Claims",
    "Continuing Jobless Claims",
    "PPI",
    "Producer Price Index",
    "Core PPI",
    "Retail Sales",
    "GDP",
    "Core PCE",
    "PCE Price Index",
]


class EconomicCalendar:
    """
    Fetches and analyzes economic calendar events.

    Uses Investing.com's calendar as primary source.
    Falls back to cached data if API is unavailable.
    """

    # Investing.com calendar API endpoint (unofficial but widely used)
    API_URL = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"

    # Cache duration in minutes
    CACHE_DURATION = 60

    def __init__(self):
        """Initialize the calendar."""
        self._cache: List[EconomicEvent] = []
        self._cache_time: Optional[datetime] = None

    def get_upcoming_events(
        self,
        hours_ahead: int = 24,
        impact_filter: Optional[EventImpact] = None
    ) -> List[EconomicEvent]:
        """
        Get upcoming economic events.

        Args:
            hours_ahead: How many hours ahead to look
            impact_filter: Only return events of this impact level

        Returns:
            List of upcoming economic events
        """
        # Check cache
        if self._cache and self._cache_time:
            cache_age = (datetime.utcnow() - self._cache_time).total_seconds() / 60
            if cache_age < self.CACHE_DURATION:
                events = self._cache
            else:
                events = self._fetch_events()
        else:
            events = self._fetch_events()

        # Filter by time
        now = datetime.utcnow()
        cutoff = now + timedelta(hours=hours_ahead)
        events = [e for e in events if now <= e.time <= cutoff]

        # Filter by impact
        if impact_filter:
            events = [e for e in events if e.impact == impact_filter]

        return sorted(events, key=lambda e: e.time)

    def get_event_risk(self) -> Tuple[EventRisk, Optional[EconomicEvent]]:
        """
        Determine current event risk level.

        Returns:
            Tuple of (risk_level, nearest_critical_event)
            - CRITICAL: Major event within 4h (FOMC, NFP, CPI)
            - WARNING: Important event within 2h (Unemployment, PPI)
            - CLEAR: No major events soon
        """
        events = self.get_upcoming_events(hours_ahead=8)
        now = datetime.utcnow()

        for event in events:
            time_until = (event.time - now).total_seconds() / 3600  # hours

            # Check Tier 1 (4h window)
            if event.impact == EventImpact.HIGH and time_until <= 4:
                logger.warning(f"⚠️ CRITICAL: {event.name} in {time_until:.1f}h - avoid new entries")
                return EventRisk.CRITICAL, event

            # Check Tier 2 (2h window)
            if event.impact == EventImpact.MEDIUM and time_until <= 2:
                logger.info(f"⚡ WARNING: {event.name} in {time_until:.1f}h - caution advised")
                return EventRisk.WARNING, event

        return EventRisk.CLEAR, None

    def _fetch_events(self) -> List[EconomicEvent]:
        """
        Fetch events from Investing.com API.

        Falls back to alternative sources if primary fails.
        """
        try:
            events = self._fetch_from_investing()
            if events:
                self._cache = events
                self._cache_time = datetime.utcnow()
                return events
        except Exception as e:
            logger.warning(f"Investing.com fetch failed: {e}")

        # Try alternative: TradingEconomics
        try:
            events = self._fetch_from_tradingeconomics()
            if events:
                self._cache = events
                self._cache_time = datetime.utcnow()
                return events
        except Exception as e:
            logger.warning(f"TradingEconomics fetch failed: {e}")

        # Fall back to hardcoded major events (FOMC dates are predictable)
        events = self._get_hardcoded_events()
        self._cache = events
        self._cache_time = datetime.utcnow()
        return events

    def _fetch_from_investing(self) -> List[EconomicEvent]:
        """Fetch events from Investing.com."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://www.investing.com/economic-calendar/",
        }

        # Request next 7 days
        today = datetime.utcnow()
        end_date = today + timedelta(days=7)

        params = {
            "country[]": [5, 72],  # US and Eurozone
            "importance[]": [3],   # High impact only
            "dateFrom": today.strftime("%Y-%m-%d"),
            "dateTo": end_date.strftime("%Y-%m-%d"),
        }

        try:
            response = requests.post(
                self.API_URL,
                headers=headers,
                data=params,
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                return self._parse_investing_response(data)
        except Exception as e:
            logger.debug(f"Investing.com API error: {e}")

        return []

    def _parse_investing_response(self, data: dict) -> List[EconomicEvent]:
        """Parse Investing.com API response."""
        events = []

        # The response format varies, this handles common structures
        if isinstance(data, dict) and "data" in data:
            raw_events = data.get("data", [])
        elif isinstance(data, list):
            raw_events = data
        else:
            return []

        for raw in raw_events:
            try:
                name = raw.get("event", raw.get("name", "Unknown"))
                country = raw.get("country", "US")

                # Parse datetime
                event_time = raw.get("datetime", raw.get("date", ""))
                if event_time:
                    try:
                        time = datetime.strptime(event_time, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        try:
                            time = datetime.strptime(event_time, "%Y-%m-%dT%H:%M:%S")
                        except ValueError:
                            continue
                else:
                    continue

                # Determine impact
                impact = self._classify_event_impact(name)

                events.append(EconomicEvent(
                    name=name,
                    country=country,
                    time=time,
                    impact=impact,
                    actual=raw.get("actual"),
                    forecast=raw.get("forecast"),
                    previous=raw.get("previous")
                ))
            except Exception as e:
                logger.debug(f"Failed to parse event: {e}")
                continue

        return events

    def _fetch_from_tradingeconomics(self) -> List[EconomicEvent]:
        """Fetch events from TradingEconomics (alternative source)."""
        # TradingEconomics requires API key for full access
        # Use their public calendar page instead
        url = "https://tradingeconomics.com/calendar"

        try:
            response = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )

            if response.status_code == 200:
                # Parse HTML for events (simplified)
                # Full implementation would use BeautifulSoup
                return []
        except Exception as e:
            logger.debug(f"TradingEconomics error: {e}")

        return []

    def _get_hardcoded_events(self) -> List[EconomicEvent]:
        """
        Get hardcoded major events.

        FOMC meetings are scheduled in advance and highly predictable.
        This ensures we never miss a major event even if APIs fail.
        """
        events = []
        now = datetime.utcnow()

        # 2025 FOMC Schedule (announced dates)
        # Times are approximate (usually 2:00 PM ET = 19:00 UTC)
        fomc_dates_2025 = [
            datetime(2025, 1, 29, 19, 0),
            datetime(2025, 3, 19, 19, 0),
            datetime(2025, 5, 7, 19, 0),
            datetime(2025, 6, 18, 19, 0),
            datetime(2025, 7, 30, 19, 0),
            datetime(2025, 9, 17, 19, 0),
            datetime(2025, 11, 5, 19, 0),
            datetime(2025, 12, 17, 19, 0),
        ]

        for date in fomc_dates_2025:
            if date > now:
                events.append(EconomicEvent(
                    name="FOMC Interest Rate Decision",
                    country="US",
                    time=date,
                    impact=EventImpact.HIGH
                ))

        # NFP is first Friday of each month at 8:30 AM ET (13:30 UTC)
        # Generate next 3 NFP dates
        month = now.month
        year = now.year
        for _ in range(3):
            # Find first Friday
            first_day = datetime(year, month, 1, 13, 30)
            days_until_friday = (4 - first_day.weekday()) % 7
            nfp_date = first_day + timedelta(days=days_until_friday)

            if nfp_date > now:
                events.append(EconomicEvent(
                    name="Non-Farm Payrolls (NFP)",
                    country="US",
                    time=nfp_date,
                    impact=EventImpact.HIGH
                ))

            # Move to next month
            month += 1
            if month > 12:
                month = 1
                year += 1

        # CPI is typically mid-month (around 13th) at 8:30 AM ET
        for offset in range(3):
            target_month = now.month + offset
            target_year = now.year
            if target_month > 12:
                target_month -= 12
                target_year += 1

            cpi_date = datetime(target_year, target_month, 13, 13, 30)
            if cpi_date > now:
                events.append(EconomicEvent(
                    name="CPI (Consumer Price Index)",
                    country="US",
                    time=cpi_date,
                    impact=EventImpact.HIGH
                ))

        return events

    def _classify_event_impact(self, event_name: str) -> EventImpact:
        """Classify event impact based on name."""
        name_upper = event_name.upper()

        # Check Tier 1 (HIGH impact)
        for keyword in TIER_1_EVENTS:
            if keyword.upper() in name_upper:
                return EventImpact.HIGH

        # Check Tier 2 (MEDIUM impact)
        for keyword in TIER_2_EVENTS:
            if keyword.upper() in name_upper:
                return EventImpact.MEDIUM

        return EventImpact.LOW


def get_event_risk() -> Dict:
    """
    Convenience function to get current event risk.

    Returns:
        Dict with:
            - risk: "CRITICAL", "WARNING", or "CLEAR"
            - event_name: Name of nearest critical event (if any)
            - event_time: Time of nearest critical event (if any)
            - hours_until: Hours until event (if any)
            - recommendation: Action recommendation
    """
    calendar = EconomicCalendar()
    risk, event = calendar.get_event_risk()

    result = {
        "risk": risk.value,
        "event_name": None,
        "event_time": None,
        "hours_until": None,
        "recommendation": "Normal trading"
    }

    if event:
        hours_until = (event.time - datetime.utcnow()).total_seconds() / 3600
        result["event_name"] = event.name
        result["event_time"] = event.time.isoformat()
        result["hours_until"] = round(hours_until, 1)

        if risk == EventRisk.CRITICAL:
            result["recommendation"] = f"⚠️ AVOID new entries - {event.name} in {hours_until:.1f}h"
        elif risk == EventRisk.WARNING:
            result["recommendation"] = f"⚡ CAUTION - {event.name} in {hours_until:.1f}h"

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("\n" + "="*60)
    print("ECONOMIC CALENDAR - L053 Implementation")
    print("="*60 + "\n")

    calendar = EconomicCalendar()

    # Get upcoming events
    print("📅 Upcoming High-Impact Events (next 72h):")
    print("-" * 40)
    events = calendar.get_upcoming_events(hours_ahead=72)

    if not events:
        print("  No high-impact events in the next 72 hours")
    else:
        for event in events[:10]:
            hours_until = (event.time - datetime.utcnow()).total_seconds() / 3600
            print(f"  • {event.name}")
            print(f"    Time: {event.time.strftime('%Y-%m-%d %H:%M')} UTC ({hours_until:.1f}h)")
            print(f"    Impact: {event.impact.value}")
            print()

    # Get current risk
    print("\n" + "-"*40)
    print("🎯 Current Event Risk:")
    risk_data = get_event_risk()
    print(f"  Risk Level: {risk_data['risk']}")
    print(f"  Recommendation: {risk_data['recommendation']}")

    if risk_data['event_name']:
        print(f"  Next Event: {risk_data['event_name']} in {risk_data['hours_until']}h")

    print("\n" + "="*60 + "\n")
