"""
Fundraising Tracker - Monitor Upcoming Token Raises

Tracks:
- Recent and upcoming fundraising rounds
- VC participation patterns
- Pre-TGE valuation data for VC markup calculation

Data Sources:
- crypto-fundraising.info
- CryptoRank (via existing integration)

Session 316: Created for "List of VC backers" Notion task
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from dacle_core.data.vc_database import get_vc_database, VCTierLevel
from dacle_core.utils.llm_cache import LLMCache

logger = logging.getLogger(__name__)


class RoundType(Enum):
    """Fundraising round types"""
    SEED = "seed"
    PRE_SEED = "pre_seed"
    SERIES_A = "series_a"
    SERIES_B = "series_b"
    SERIES_C = "series_c"
    PRIVATE_SALE = "private_sale"
    STRATEGIC = "strategic"
    MA = "m&a"  # Mergers & Acquisitions
    UNKNOWN = "unknown"


@dataclass
class FundraisingRound:
    """Represents a fundraising round"""
    project_name: str
    token_symbol: Optional[str]
    round_type: RoundType
    amount_usd: float
    valuation_usd: Optional[float] = None
    date: Optional[str] = None  # ISO date string
    investors: List[str] = field(default_factory=list)
    lead_investor: Optional[str] = None
    category: Optional[str] = None  # e.g., "DeFi", "L1", "Payment"
    source: str = "unknown"
    notes: Optional[str] = None


@dataclass
class FundraisingProject:
    """Project with multiple funding rounds"""
    name: str
    symbol: Optional[str]
    category: Optional[str]
    total_raised_usd: float
    rounds: List[FundraisingRound] = field(default_factory=list)
    all_investors: List[str] = field(default_factory=list)
    tier_1_investor_count: int = 0
    tge_status: Optional[str] = None  # "announced", "upcoming", "completed"
    tge_date: Optional[str] = None
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())


class FundraisingTracker:
    """
    Track and analyze upcoming fundraising rounds

    Use cases:
    - Discover pre-TGE opportunities early
    - Calculate VC markup (seed valuation -> TGE FDV)
    - Identify tier 1 VC backing patterns
    """

    DATA_FILE = Path(__file__).parent.parent.parent / "data" / "fundraising_tracker.json"
    CACHE_TTL = 3600 * 6  # 6 hours

    def __init__(self):
        """Initialize fundraising tracker"""
        self.projects: Dict[str, FundraisingProject] = {}
        self.cache = LLMCache("fundraising")
        self.vc_db = get_vc_database()
        self._load_data()

    def _load_data(self):
        """Load existing data from file"""
        if self.DATA_FILE.exists():
            try:
                with open(self.DATA_FILE, 'r') as f:
                    data = json.load(f)

                for name, proj_data in data.get('projects', {}).items():
                    rounds_data = proj_data.pop('rounds', [])
                    rounds = []
                    for rd in rounds_data:
                        rd['round_type'] = RoundType(rd['round_type'])
                        rounds.append(FundraisingRound(**rd))
                    proj_data['rounds'] = rounds
                    self.projects[name] = FundraisingProject(**proj_data)

                logger.info(f"Loaded {len(self.projects)} fundraising projects")
            except Exception as e:
                logger.error(f"Failed to load fundraising data: {e}")

    def _save_data(self):
        """Save data to file"""
        try:
            self.DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

            data = {
                'metadata': {
                    'last_updated': datetime.now().isoformat(),
                    'total_projects': len(self.projects),
                    'total_raised': sum(p.total_raised_usd for p in self.projects.values())
                },
                'projects': {}
            }

            for name, proj in self.projects.items():
                proj_dict = asdict(proj)
                for rd in proj_dict['rounds']:
                    rd['round_type'] = rd['round_type'].value if isinstance(rd['round_type'], RoundType) else rd['round_type']
                data['projects'][name] = proj_dict

            with open(self.DATA_FILE, 'w') as f:
                json.dump(data, f, indent=2)

            logger.info(f"Saved {len(self.projects)} fundraising projects")
        except Exception as e:
            logger.error(f"Failed to save fundraising data: {e}")

    def add_round(
        self,
        project_name: str,
        round_type: str,
        amount_usd: float,
        investors: List[str],
        token_symbol: Optional[str] = None,
        valuation_usd: Optional[float] = None,
        date: Optional[str] = None,
        lead_investor: Optional[str] = None,
        category: Optional[str] = None,
        source: str = "manual"
    ) -> FundraisingProject:
        """Add a fundraising round to tracking"""

        # Parse round type
        try:
            rt = RoundType(round_type.lower().replace(" ", "_").replace("-", "_"))
        except ValueError:
            rt = RoundType.UNKNOWN

        round_obj = FundraisingRound(
            project_name=project_name,
            token_symbol=token_symbol,
            round_type=rt,
            amount_usd=amount_usd,
            valuation_usd=valuation_usd,
            date=date or datetime.now().isoformat()[:10],
            investors=investors,
            lead_investor=lead_investor or (investors[0] if investors else None),
            category=category,
            source=source
        )

        # Get or create project
        if project_name in self.projects:
            proj = self.projects[project_name]
            proj.rounds.append(round_obj)
            proj.total_raised_usd += amount_usd

            # Update investors list
            for inv in investors:
                if inv not in proj.all_investors:
                    proj.all_investors.append(inv)
        else:
            # Classify investors
            classification = self.vc_db.classify_investor_list(investors)

            proj = FundraisingProject(
                name=project_name,
                symbol=token_symbol,
                category=category,
                total_raised_usd=amount_usd,
                rounds=[round_obj],
                all_investors=investors.copy(),
                tier_1_investor_count=classification['tier_1_count']
            )
            self.projects[project_name] = proj

        # Recalculate tier 1 count
        classification = self.vc_db.classify_investor_list(proj.all_investors)
        proj.tier_1_investor_count = classification['tier_1_count']
        proj.last_updated = datetime.now().isoformat()

        self._save_data()
        return proj

    def get_recent_rounds(self, days: int = 30) -> List[FundraisingRound]:
        """Get rounds from the last N days"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()[:10]
        recent = []

        for proj in self.projects.values():
            for rd in proj.rounds:
                if rd.date and rd.date >= cutoff:
                    recent.append(rd)

        return sorted(recent, key=lambda x: x.date or '', reverse=True)

    def get_largest_rounds(self, limit: int = 10) -> List[FundraisingRound]:
        """Get largest fundraising rounds"""
        all_rounds = []
        for proj in self.projects.values():
            all_rounds.extend(proj.rounds)

        return sorted(all_rounds, key=lambda x: x.amount_usd, reverse=True)[:limit]

    def get_projects_by_vc(self, vc_name: str) -> List[FundraisingProject]:
        """Find all projects a VC has invested in"""
        vc_lower = vc_name.lower()
        return [
            proj for proj in self.projects.values()
            if any(vc_lower in inv.lower() for inv in proj.all_investors)
        ]

    def get_tier_1_backed(self, min_tier_1: int = 1) -> List[FundraisingProject]:
        """Get projects with minimum tier 1 VC count"""
        return [
            proj for proj in self.projects.values()
            if proj.tier_1_investor_count >= min_tier_1
        ]

    def calculate_vc_markup(self, project_name: str, tge_fdv: float) -> Optional[Dict]:
        """
        Calculate VC markup from earliest round to TGE FDV

        Returns:
            {
                'seed_valuation': float,
                'tge_fdv': float,
                'markup': float,  # e.g., 10.0 = 10x
                'seed_round': FundraisingRound,
                'total_raised': float
            }
        """
        proj = self.projects.get(project_name)
        if not proj or not proj.rounds:
            return None

        # Find earliest round with valuation
        earliest = None
        for rd in proj.rounds:
            if rd.valuation_usd:
                if earliest is None or (rd.date and earliest.date and rd.date < earliest.date):
                    earliest = rd

        if not earliest or not earliest.valuation_usd:
            return None

        markup = tge_fdv / earliest.valuation_usd

        return {
            'seed_valuation': earliest.valuation_usd,
            'seed_round_type': earliest.round_type.value,
            'tge_fdv': tge_fdv,
            'markup': round(markup, 2),
            'total_raised': proj.total_raised_usd,
            'investors': proj.all_investors
        }

    def import_from_crypto_fundraising(self, data: List[Dict]):
        """
        Import fundraising data from crypto-fundraising.info format

        Expected format per item:
        {
            'project_name': str,
            'token_symbol': str (optional),
            'round_type': str,
            'amount_usd': float,
            'investors': List[str],
            'date': str (optional),
            'category': str (optional)
        }
        """
        imported = 0
        for item in data:
            try:
                self.add_round(
                    project_name=item['project_name'],
                    round_type=item.get('round_type', 'unknown'),
                    amount_usd=item.get('amount_usd', 0),
                    investors=item.get('investors', []),
                    token_symbol=item.get('token_symbol'),
                    date=item.get('date'),
                    category=item.get('category'),
                    source='crypto-fundraising.info'
                )
                imported += 1
            except Exception as e:
                logger.warning(f"Failed to import {item.get('project_name', 'unknown')}: {e}")

        logger.info(f"Imported {imported} rounds from crypto-fundraising.info")
        return imported

    def seed_with_known_rounds(self):
        """Seed database with known recent fundraising rounds"""

        # Recent major rounds (from crypto-fundraising.info Dec 2025 - Jan 2026)
        known_rounds = [
            {
                'project_name': 'Rain',
                'round_type': 'series_c',
                'amount_usd': 250_000_000,
                'investors': ['ICONIQ Capital', 'Sapphire Ventures', 'Dragonfly Capital',
                             'Bessemer Venture Partners', 'Galaxy Digital'],
                'date': '2026-01',
                'category': 'Payment/Stablecoin'
            },
            {
                'project_name': 'BlackOpal',
                'round_type': 'unknown',
                'amount_usd': 200_000_000,
                'investors': [],
                'date': '2026-01',
                'category': 'Finance/RWA'
            },
            {
                'project_name': 'HashKey Group',
                'round_type': 'strategic',
                'amount_usd': 250_000_000,
                'investors': [],
                'date': '2025-12',
                'category': 'Exchange/Infrastructure'
            },
            {
                'project_name': 'Tres Finance',
                'round_type': 'm&a',
                'amount_usd': 130_000_000,
                'investors': [],
                'date': '2026-01',
                'category': 'Finance'
            },
            {
                'project_name': 'Monad',
                'token_symbol': 'MONAD',
                'round_type': 'series_a',
                'amount_usd': 225_000_000,
                'investors': ['Paradigm', 'Dragonfly Capital', 'Electric Capital', 'Galaxy Digital'],
                'date': '2024-04',
                'category': 'L1',
                'valuation': 3_000_000_000
            },
            {
                'project_name': 'Berachain',
                'token_symbol': 'BERA',
                'round_type': 'series_b',
                'amount_usd': 100_000_000,
                'investors': ['Polychain Capital', 'Framework Ventures', 'Delphi Ventures',
                             'Hack VC', 'Tribe Capital'],
                'date': '2024-03',
                'category': 'L1',
                'valuation': 1_500_000_000
            },
            {
                'project_name': 'EigenLayer',
                'token_symbol': 'EIGEN',
                'round_type': 'series_a',
                'amount_usd': 50_000_000,
                'investors': ['Blockchain Capital', 'Electric Capital', 'Polychain Capital',
                             'Coinbase Ventures', 'Hack VC'],
                'date': '2023-03',
                'category': 'Infrastructure'
            },
            {
                'project_name': 'Celestia',
                'token_symbol': 'TIA',
                'round_type': 'series_a',
                'amount_usd': 55_000_000,
                'investors': ['a16z Crypto', 'Paradigm', 'Polychain Capital', 'Bain Capital Crypto'],
                'date': '2022-10',
                'category': 'Modular/DA'
            },
        ]

        for rd in known_rounds:
            valuation = rd.pop('valuation', None)
            self.add_round(**rd, valuation_usd=valuation, source='known_data')

        logger.info(f"Seeded {len(known_rounds)} known fundraising rounds")

    def get_statistics(self) -> Dict:
        """Get tracker statistics"""
        total_raised = sum(p.total_raised_usd for p in self.projects.values())
        tier_1_projects = len(self.get_tier_1_backed(min_tier_1=1))

        # Category breakdown
        categories = {}
        for proj in self.projects.values():
            cat = proj.category or 'Unknown'
            categories[cat] = categories.get(cat, 0) + 1

        return {
            'total_projects': len(self.projects),
            'total_raised_usd': total_raised,
            'tier_1_backed_projects': tier_1_projects,
            'categories': categories,
            'last_updated': datetime.now().isoformat()
        }


# Singleton instance
_tracker: Optional[FundraisingTracker] = None


def get_fundraising_tracker() -> FundraisingTracker:
    """Get singleton tracker instance"""
    global _tracker
    if _tracker is None:
        _tracker = FundraisingTracker()
    return _tracker


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    tracker = get_fundraising_tracker()

    # Seed with known data if empty
    if not tracker.projects:
        tracker.seed_with_known_rounds()

    print("\n--- Fundraising Tracker Statistics ---")
    stats = tracker.get_statistics()
    print(f"Total Projects: {stats['total_projects']}")
    print(f"Total Raised: ${stats['total_raised_usd']:,.0f}")
    print(f"Tier 1 Backed: {stats['tier_1_backed_projects']}")

    print("\n--- Largest Rounds ---")
    for rd in tracker.get_largest_rounds(5):
        print(f"  {rd.project_name}: ${rd.amount_usd:,.0f} ({rd.round_type.value})")

    print("\n--- VC Markup Example (Monad) ---")
    # Assuming $10B TGE FDV
    markup = tracker.calculate_vc_markup('Monad', 10_000_000_000)
    if markup:
        print(f"  Seed Valuation: ${markup['seed_valuation']:,.0f}")
        print(f"  TGE FDV: ${markup['tge_fdv']:,.0f}")
        print(f"  Markup: {markup['markup']}x")
