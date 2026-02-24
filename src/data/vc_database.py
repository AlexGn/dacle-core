"""
VC Database - Comprehensive Venture Capital Fund Registry

Provides:
- Expanded VC list with tier classification
- Tier-based filtering for investment analysis
- AUM tracking and investment patterns
- Integration with CryptoRank and other data sources

Session 316: Created for "List of VC backers" Notion task
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class VCTierLevel(Enum):
    """VC Tier Classification"""
    TIER_1_TOP = "tier_1_top"       # >$1B AUM, elite track record
    TIER_1_UPPER = "tier_1_upper"   # $500M-$1B AUM, strong track record
    TIER_1_SPECIALIZED = "tier_1_specialized"  # Specialized/vertical-focused
    TIER_2 = "tier_2"               # Exchange VCs, regional leaders
    TIER_3 = "tier_3"               # Smaller/newer VCs
    PREDATORY = "predatory"         # Known for market manipulation


@dataclass
class VCFund:
    """Represents a Venture Capital fund"""
    name: str
    tier: VCTierLevel
    aliases: List[str] = field(default_factory=list)
    aum_usd: Optional[str] = None  # e.g., "$7.6B"
    investment_count: Optional[int] = None
    key_investments: List[str] = field(default_factory=list)
    focus_areas: List[str] = field(default_factory=list)
    headquarters: Optional[str] = None
    founded_year: Optional[int] = None
    conviction_modifier: float = 0.0  # Score adjustment when found in project
    dump_risk: Optional[str] = None  # LOW/MEDIUM/HIGH
    notes: Optional[str] = None
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())


class VCDatabase:
    """
    Comprehensive VC Database with tier filtering

    Features:
    - 100+ VCs classified by tier
    - Alias matching for fuzzy name resolution
    - Investment tracking
    - Conviction scoring integration
    """

    DATA_FILE = Path(__file__).parent.parent.parent / "data" / "vc_comprehensive_database.json"

    def __init__(self):
        """Initialize VC database"""
        self.vcs: Dict[str, VCFund] = {}
        self._alias_map: Dict[str, str] = {}  # alias -> canonical name
        self._initialize_database()

    def _initialize_database(self):
        """Load or create the VC database"""
        if self.DATA_FILE.exists():
            self._load_from_file()
        else:
            self._create_default_database()
            self._save_to_file()

    def _create_default_database(self):
        """Create comprehensive VC database with all known funds"""

        # ===========================================
        # TIER 1 TOP - Elite VCs ($1B+ AUM)
        # ===========================================
        tier_1_top = [
            VCFund(
                name="Paradigm",
                tier=VCTierLevel.TIER_1_TOP,
                aliases=["Paradigm Capital", "paradigm.xyz"],
                aum_usd="$13.2B",
                investment_count=90,
                key_investments=["Celestia", "Coinbase", "Uniswap", "dYdX", "Optimism", "zkSync", "Starknet", "Monad"],
                focus_areas=["DeFi", "L1/L2", "Infrastructure"],
                headquarters="San Francisco",
                founded_year=2018,
                conviction_modifier=0.5,
                dump_risk="HIGH",
                notes="100% of Paradigm tokens >$10B FDV dumped -40% to -86%"
            ),
            VCFund(
                name="a16z Crypto",
                tier=VCTierLevel.TIER_1_TOP,
                aliases=["Andreessen Horowitz", "a16z", "a16zcrypto", "Andreessen Horowitz Crypto"],
                aum_usd="$7.6B",
                investment_count=200,
                key_investments=["Coinbase", "OpenSea", "MakerDAO", "Solana", "Celestia", "Optimism", "Uniswap"],
                focus_areas=["DeFi", "NFT", "Gaming", "Infrastructure"],
                headquarters="Menlo Park",
                founded_year=2018,
                conviction_modifier=0.5,
                dump_risk="HIGH",
                notes="Largest crypto fund ever raised"
            ),
            VCFund(
                name="Pantera Capital",
                tier=VCTierLevel.TIER_1_TOP,
                aliases=["Pantera"],
                aum_usd="$3B+",
                investment_count=150,
                key_investments=["Solana", "Polkadot", "Avalanche", "1inch", "Balancer", "Ondo"],
                focus_areas=["DeFi", "L1", "Infrastructure"],
                headquarters="Menlo Park",
                founded_year=2013,
                conviction_modifier=0.4,
                dump_risk="MEDIUM",
                notes="Oldest US crypto VC (2013); 2,303% ROI in 2017"
            ),
            VCFund(
                name="Dragonfly Capital",
                tier=VCTierLevel.TIER_1_TOP,
                aliases=["Dragonfly", "Dragonfly Crypto"],
                aum_usd="$3B+",
                investment_count=160,
                key_investments=["Compound", "dYdX", "MakerDAO", "Celo", "Near", "Monad"],
                focus_areas=["DeFi", "Infrastructure", "Cross-chain"],
                headquarters="San Francisco",
                founded_year=2018,
                conviction_modifier=0.4,
                dump_risk="MEDIUM",
                notes="Strong Asia presence; 160+ crypto startups"
            ),
            VCFund(
                name="Electric Capital",
                tier=VCTierLevel.TIER_1_TOP,
                aliases=["Electric"],
                aum_usd="$1B+",
                investment_count=80,
                key_investments=["Uniswap", "Aave", "NEAR", "Dapper Labs", "Kraken"],
                focus_areas=["DeFi", "Developer tools", "Infrastructure"],
                headquarters="Palo Alto",
                founded_year=2018,
                conviction_modifier=0.3,
                dump_risk="MEDIUM",
                notes="56% AUM growth 2023-2024; strong dev ecosystem focus"
            ),
            VCFund(
                name="Polychain Capital",
                tier=VCTierLevel.TIER_1_TOP,
                aliases=["Polychain"],
                aum_usd="$600M+",
                investment_count=100,
                key_investments=["Ethereum", "Filecoin", "Compound", "Maker", "Polkadot", "Berachain"],
                focus_areas=["L1", "DeFi", "Privacy"],
                headquarters="San Francisco",
                founded_year=2016,
                conviction_modifier=0.4,
                dump_risk="MEDIUM",
                notes="Founded by first Coinbase employee (Olaf Carlson-Wee)"
            ),
        ]

        # ===========================================
        # TIER 1 UPPER - Strong Track Record ($500M-$1B)
        # ===========================================
        tier_1_upper = [
            VCFund(
                name="Multicoin Capital",
                tier=VCTierLevel.TIER_1_UPPER,
                aliases=["Multicoin"],
                aum_usd="$600M",
                investment_count=60,
                key_investments=["Solana", "Helium", "Aptos", "StarkWare", "LayerZero"],
                focus_areas=["L1", "Infrastructure", "DePIN"],
                headquarters="Austin",
                founded_year=2017,
                conviction_modifier=0.3,
                dump_risk="MEDIUM",
                notes="Led Solana Series A; 11 unicorns"
            ),
            VCFund(
                name="Haun Ventures",
                tier=VCTierLevel.TIER_1_UPPER,
                aliases=["Haun", "Katie Haun"],
                aum_usd="$1.5B",
                investment_count=30,
                key_investments=["Coinbase", "Uniswap", "OpenSea", "LayerZero"],
                focus_areas=["DeFi", "Infrastructure", "Regulatory"],
                headquarters="San Francisco",
                founded_year=2022,
                conviction_modifier=0.3,
                dump_risk="MEDIUM",
                notes="Ex-a16z partner Katie Haun; crypto-native regulatory expertise"
            ),
            VCFund(
                name="Galaxy Digital",
                tier=VCTierLevel.TIER_1_UPPER,
                aliases=["Galaxy", "Galaxy Ventures"],
                aum_usd="$7.8B",
                investment_count=220,
                key_investments=["M^Zero", "Monad", "Ethena", "Plume", "Rain"],
                focus_areas=["Infrastructure", "DeFi", "Trading"],
                headquarters="New York",
                founded_year=2018,
                conviction_modifier=0.2,
                dump_risk="MEDIUM",
                notes="220+ portfolio companies; Mike Novogratz"
            ),
            VCFund(
                name="Coinbase Ventures",
                tier=VCTierLevel.TIER_1_UPPER,
                aliases=["Coinbase VC", "CB Ventures"],
                aum_usd="$500M+",
                investment_count=400,
                key_investments=["Compound", "dYdX", "Messari", "Optimism", "OpenSea"],
                focus_areas=["DeFi", "Infrastructure", "Exchanges"],
                headquarters="San Francisco",
                founded_year=2018,
                conviction_modifier=0.2,
                dump_risk="LOW",
                notes="Exchange-backed; strong network effects"
            ),
            VCFund(
                name="Binance Labs",
                tier=VCTierLevel.TIER_1_UPPER,
                aliases=["Binance", "BNB Labs"],
                aum_usd="$7.5B",
                investment_count=200,
                key_investments=["Polygon", "1inch", "Axie Infinity", "The Sandbox"],
                focus_areas=["DeFi", "Gaming", "BSC ecosystem"],
                headquarters="UAE",
                founded_year=2018,
                conviction_modifier=0.2,
                dump_risk="MEDIUM",
                notes="Largest exchange VC; BSC ecosystem builder"
            ),
            VCFund(
                name="Sequoia Capital",
                tier=VCTierLevel.TIER_1_UPPER,
                aliases=["Sequoia", "Sequoia Crypto"],
                aum_usd="$85B",
                investment_count=50,
                key_investments=["FTX", "Polygon", "LayerZero"],
                focus_areas=["Infrastructure", "DeFi"],
                headquarters="Menlo Park",
                founded_year=1972,
                conviction_modifier=0.3,
                dump_risk="MEDIUM",
                notes="Traditional VC entering crypto"
            ),
        ]

        # ===========================================
        # TIER 1 SPECIALIZED - Vertical Focused
        # ===========================================
        tier_1_specialized = [
            VCFund(
                name="Framework Ventures",
                tier=VCTierLevel.TIER_1_SPECIALIZED,
                aliases=["Framework"],
                investment_count=193,
                key_investments=["Chainlink", "Synthetix", "NEAR", "Aave", "Berachain"],
                focus_areas=["DeFi", "Oracles", "Infrastructure"],
                headquarters="San Francisco",
                founded_year=2019,
                conviction_modifier=0.25,
                dump_risk="MEDIUM",
                notes="193 investments; 27 in 2024 alone"
            ),
            VCFund(
                name="1kx",
                tier=VCTierLevel.TIER_1_SPECIALIZED,
                aliases=["1kx Network", "One KX"],
                investment_count=60,
                key_investments=["Astria", "Lagrange Labs", "Lido", "Eigenlayer"],
                focus_areas=["Infrastructure", "Staking", "L2"],
                headquarters="Berlin",
                founded_year=2019,
                conviction_modifier=0.25,
                dump_risk="MEDIUM",
                notes="Backed by Marc Andreessen, Galaxy"
            ),
            VCFund(
                name="Delphi Ventures",
                tier=VCTierLevel.TIER_1_SPECIALIZED,
                aliases=["Delphi Digital", "Delphi"],
                investment_count=100,
                key_investments=["Axie Infinity", "STEPN", "Solana", "Berachain", "Blur"],
                focus_areas=["Gaming", "DeFi", "NFT"],
                headquarters="New York",
                founded_year=2020,
                conviction_modifier=0.2,
                dump_risk="MEDIUM",
                notes="100 investments in 4 years; strong research"
            ),
            VCFund(
                name="Foresight Ventures",
                tier=VCTierLevel.TIER_1_SPECIALIZED,
                aliases=["Foresight"],
                investment_count=80,
                key_investments=["zkSync", "Scroll", "Manta Network", "EigenLayer"],
                focus_areas=["L2", "ZK", "Infrastructure"],
                headquarters="Singapore",
                founded_year=2021,
                conviction_modifier=0.2,
                dump_risk="MEDIUM",
                notes="Top 5 most active crypto VC globally"
            ),
            VCFund(
                name="Blockchain Capital",
                tier=VCTierLevel.TIER_1_SPECIALIZED,
                aliases=["Blockchain Cap", "BCAP"],
                aum_usd="$2B+",
                investment_count=150,
                key_investments=["Coinbase", "Kraken", "Circle", "OpenSea", "Ripple"],
                focus_areas=["Infrastructure", "Exchanges", "DeFi"],
                headquarters="San Francisco",
                founded_year=2013,
                conviction_modifier=0.25,
                dump_risk="LOW",
                notes="One of earliest crypto VCs (2013)"
            ),
            VCFund(
                name="Hack VC",
                tier=VCTierLevel.TIER_1_SPECIALIZED,
                aliases=["HackVC", "Hack Ventures"],
                aum_usd="$500M+",
                investment_count=100,
                key_investments=["Sentient", "EigenLayer", "Movement"],
                focus_areas=["AI", "Infrastructure", "L2"],
                headquarters="San Francisco",
                founded_year=2018,
                conviction_modifier=0.2,
                dump_risk="MEDIUM",
                notes="Active in crypto AI intersection"
            ),
            VCFund(
                name="The Spartan Group",
                tier=VCTierLevel.TIER_1_SPECIALIZED,
                aliases=["Spartan Group", "Spartan"],
                aum_usd="$500M",
                investment_count=70,
                key_investments=["Synthetix", "Aave", "Chainlink"],
                focus_areas=["DeFi", "NFT", "Infrastructure"],
                headquarters="Singapore",
                founded_year=2017,
                conviction_modifier=0.2,
                dump_risk="MEDIUM",
                notes="Asia-focused, DeFi/NFT/infrastructure"
            ),
            VCFund(
                name="Jump Crypto",
                tier=VCTierLevel.TIER_1_SPECIALIZED,
                aliases=["Jump Trading", "Jump"],
                aum_usd="$500M+",
                investment_count=50,
                key_investments=["Solana", "Wormhole", "Pyth", "Aptos"],
                focus_areas=["Infrastructure", "Bridges", "Oracles"],
                headquarters="Chicago",
                founded_year=2021,
                conviction_modifier=0.2,
                dump_risk="MEDIUM",
                notes="HFT background, deep liquidity provision"
            ),
            VCFund(
                name="Animoca Brands",
                tier=VCTierLevel.TIER_1_SPECIALIZED,
                aliases=["Animoca"],
                aum_usd="$4.5B",
                investment_count=400,
                key_investments=["The Sandbox", "Axie Infinity", "OpenSea", "Dapper Labs"],
                focus_areas=["Gaming", "NFT", "Metaverse"],
                headquarters="Hong Kong",
                founded_year=2014,
                conviction_modifier=0.15,
                dump_risk="HIGH",
                notes="Largest gaming/metaverse investor"
            ),
        ]

        # ===========================================
        # TIER 2 - Exchange VCs & Regional Leaders
        # ===========================================
        tier_2 = [
            VCFund(
                name="OKX Ventures",
                tier=VCTierLevel.TIER_2,
                aliases=["OKX", "OKEx Ventures"],
                investment_count=150,
                key_investments=["Sui", "LayerZero", "Celestia"],
                focus_areas=["Infrastructure", "DeFi"],
                headquarters="Singapore",
                conviction_modifier=0.1,
                dump_risk="MEDIUM"
            ),
            VCFund(
                name="Hashed",
                tier=VCTierLevel.TIER_2,
                aliases=["Hashed Fund"],
                aum_usd="$320M",
                investment_count=100,
                key_investments=["Axie Infinity", "Klaytn", "Terra"],
                focus_areas=["Gaming", "DeFi", "Korea ecosystem"],
                headquarters="Seoul",
                conviction_modifier=0.1,
                dump_risk="MEDIUM",
                notes="Leading Korean crypto VC"
            ),
            VCFund(
                name="Maven 11 Capital",
                tier=VCTierLevel.TIER_2,
                aliases=["Maven 11", "Maven11"],
                investment_count=40,
                key_investments=["StarkWare", "Celestia", "Eigenlayer"],
                focus_areas=["Infrastructure", "L2", "ZK"],
                headquarters="Amsterdam",
                conviction_modifier=0.1,
                dump_risk="MEDIUM"
            ),
            VCFund(
                name="Borderless Capital",
                tier=VCTierLevel.TIER_2,
                aliases=["Borderless"],
                aum_usd="$1.5B",
                investment_count=150,
                key_investments=["Algorand", "Tinyman", "Folks Finance"],
                focus_areas=["Algorand ecosystem", "DeFi"],
                headquarters="Miami",
                conviction_modifier=0.1,
                dump_risk="MEDIUM",
                notes="Algorand ecosystem focused"
            ),
            VCFund(
                name="Anchorage Digital",
                tier=VCTierLevel.TIER_2,
                aliases=["Anchorage"],
                investment_count=20,
                key_investments=["Various custody clients"],
                focus_areas=["Custody", "Infrastructure"],
                headquarters="San Francisco",
                conviction_modifier=0.1,
                dump_risk="LOW"
            ),
            VCFund(
                name="Generative Ventures",
                tier=VCTierLevel.TIER_2,
                aliases=["Generative"],
                investment_count=30,
                key_investments=["AI projects"],
                focus_areas=["AI", "Infrastructure"],
                conviction_modifier=0.1,
                dump_risk="MEDIUM"
            ),
            VCFund(
                name="YZI Labs",
                tier=VCTierLevel.TIER_2,
                aliases=["YZI", "Yzi Labs"],
                investment_count=50,
                key_investments=["Various DeFi"],
                focus_areas=["DeFi", "Infrastructure"],
                conviction_modifier=0.1,
                dump_risk="MEDIUM",
                notes="10 projects invested (Dec 2025)"
            ),
            VCFund(
                name="ICONIQ Capital",
                tier=VCTierLevel.TIER_2,
                aliases=["ICONIQ", "Iconiq Growth"],
                aum_usd="$80B",
                investment_count=30,
                key_investments=["Rain", "Stripe"],
                focus_areas=["Payments", "Fintech"],
                headquarters="San Francisco",
                conviction_modifier=0.15,
                dump_risk="LOW",
                notes="Family office/traditional VC entering crypto"
            ),
            VCFund(
                name="Sapphire Ventures",
                tier=VCTierLevel.TIER_2,
                aliases=["Sapphire"],
                aum_usd="$8B+",
                investment_count=20,
                key_investments=["Rain"],
                focus_areas=["Fintech", "Enterprise"],
                headquarters="Palo Alto",
                conviction_modifier=0.1,
                dump_risk="LOW"
            ),
            VCFund(
                name="Bessemer Venture Partners",
                tier=VCTierLevel.TIER_2,
                aliases=["Bessemer", "BVP"],
                aum_usd="$15B+",
                investment_count=25,
                key_investments=["Rain", "Coinbase"],
                focus_areas=["Infrastructure", "Fintech"],
                headquarters="Menlo Park",
                conviction_modifier=0.1,
                dump_risk="LOW"
            ),
            VCFund(
                name="Auros Global",
                tier=VCTierLevel.TIER_2,
                aliases=["Auros"],
                investment_count=40,
                focus_areas=["Market Making", "Trading"],
                headquarters="Hong Kong",
                conviction_modifier=0.05,
                dump_risk="MEDIUM",
                notes="Market maker + investor"
            ),
            VCFund(
                name="GSR Markets",
                tier=VCTierLevel.TIER_2,
                aliases=["GSR", "GSR Markets Ltd"],
                investment_count=50,
                focus_areas=["Market Making", "Trading"],
                headquarters="Hong Kong",
                conviction_modifier=0.0,
                dump_risk="HIGH",
                notes="Market maker - watch for manipulation"
            ),
            VCFund(
                name="Hashkey Capital",
                tier=VCTierLevel.TIER_2,
                aliases=["Hashkey", "HashKey Group"],
                aum_usd="$500M+",
                investment_count=100,
                key_investments=["Polkadot", "Cosmos"],
                focus_areas=["Infrastructure", "DeFi"],
                headquarters="Hong Kong",
                conviction_modifier=0.1,
                dump_risk="MEDIUM",
                notes="Raised $250M (Dec 2025)"
            ),
        ]

        # ===========================================
        # TIER 3 - Smaller/Newer VCs
        # ===========================================
        tier_3 = [
            VCFund(
                name="Placeholder VC",
                tier=VCTierLevel.TIER_3,
                aliases=["Placeholder"],
                investment_count=30,
                focus_areas=["Infrastructure"],
                conviction_modifier=0.05,
                dump_risk="MEDIUM"
            ),
            VCFund(
                name="Mechanism Capital",
                tier=VCTierLevel.TIER_3,
                aliases=["Mechanism"],
                investment_count=50,
                focus_areas=["DeFi", "Gaming"],
                conviction_modifier=0.05,
                dump_risk="MEDIUM"
            ),
            VCFund(
                name="IOSG Ventures",
                tier=VCTierLevel.TIER_3,
                aliases=["IOSG"],
                investment_count=80,
                focus_areas=["Infrastructure", "DeFi"],
                headquarters="Berlin",
                conviction_modifier=0.05,
                dump_risk="MEDIUM"
            ),
            VCFund(
                name="Shima Capital",
                tier=VCTierLevel.TIER_3,
                aliases=["Shima"],
                investment_count=40,
                focus_areas=["DeFi", "Gaming"],
                conviction_modifier=0.05,
                dump_risk="MEDIUM"
            ),
            VCFund(
                name="CMS Holdings",
                tier=VCTierLevel.TIER_3,
                aliases=["CMS"],
                investment_count=60,
                focus_areas=["DeFi", "Trading"],
                conviction_modifier=0.05,
                dump_risk="MEDIUM"
            ),
            VCFund(
                name="Amber Group",
                tier=VCTierLevel.TIER_3,
                aliases=["Amber"],
                investment_count=30,
                focus_areas=["Trading", "DeFi"],
                conviction_modifier=0.0,
                dump_risk="MEDIUM",
                notes="Trading firm + investor"
            ),
        ]

        # ===========================================
        # PREDATORY VCs - Known for manipulation
        # ===========================================
        predatory = [
            VCFund(
                name="DWF Labs",
                tier=VCTierLevel.PREDATORY,
                aliases=["DWF", "DWF Ventures"],
                investment_count=200,
                focus_areas=["Market Making", "Various"],
                conviction_modifier=-0.5,
                dump_risk="CRITICAL",
                notes="Known for market manipulation, pump and dump patterns"
            ),
            VCFund(
                name="Gotbit",
                tier=VCTierLevel.PREDATORY,
                aliases=["GotBit"],
                investment_count=50,
                focus_areas=["Market Making"],
                conviction_modifier=-0.5,
                dump_risk="CRITICAL",
                notes="Market maker with manipulation allegations"
            ),
            VCFund(
                name="Wintermute",
                tier=VCTierLevel.PREDATORY,
                aliases=["Wintermute Trading"],
                investment_count=100,
                focus_areas=["Market Making"],
                conviction_modifier=-0.3,
                dump_risk="HIGH",
                notes="Market maker - aggressive selling patterns observed"
            ),
        ]

        # Add all VCs to database
        all_vcs = tier_1_top + tier_1_upper + tier_1_specialized + tier_2 + tier_3 + predatory
        for vc in all_vcs:
            self.vcs[vc.name] = vc
            # Build alias map
            for alias in vc.aliases:
                self._alias_map[alias.lower()] = vc.name
            self._alias_map[vc.name.lower()] = vc.name

    def _load_from_file(self):
        """Load database from JSON file"""
        try:
            with open(self.DATA_FILE, 'r') as f:
                data = json.load(f)

            for name, vc_data in data.get('vcs', {}).items():
                tier = VCTierLevel(vc_data.pop('tier'))
                # Session 316: Pop 'name' from vc_data to avoid duplicate kwarg error
                vc_data.pop('name', None)
                vc = VCFund(name=name, tier=tier, **vc_data)
                self.vcs[name] = vc
                for alias in vc.aliases:
                    self._alias_map[alias.lower()] = name
                self._alias_map[name.lower()] = name

            logger.info(f"Loaded {len(self.vcs)} VCs from database")
        except Exception as e:
            logger.error(f"Failed to load VC database: {e}")
            self._create_default_database()

    def _save_to_file(self):
        """Save database to JSON file"""
        try:
            self.DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

            data = {
                'metadata': {
                    'version': '2.0',
                    'last_updated': datetime.now().isoformat(),
                    'total_vcs': len(self.vcs),
                    'tier_counts': self.get_tier_counts()
                },
                'vcs': {}
            }

            for name, vc in self.vcs.items():
                vc_dict = asdict(vc)
                vc_dict['tier'] = vc.tier.value
                data['vcs'][name] = vc_dict

            with open(self.DATA_FILE, 'w') as f:
                json.dump(data, f, indent=2)

            logger.info(f"Saved {len(self.vcs)} VCs to {self.DATA_FILE}")
        except Exception as e:
            logger.error(f"Failed to save VC database: {e}")

    # ========================================
    # Query Methods
    # ========================================

    def get_vc(self, name: str) -> Optional[VCFund]:
        """Get VC by name or alias"""
        canonical = self._alias_map.get(name.lower())
        if canonical:
            return self.vcs.get(canonical)
        return self.vcs.get(name)

    def filter_by_tier(self, tier: VCTierLevel) -> List[VCFund]:
        """Get all VCs of a specific tier"""
        return [vc for vc in self.vcs.values() if vc.tier == tier]

    def filter_by_tiers(self, tiers: List[VCTierLevel]) -> List[VCFund]:
        """Get all VCs matching any of the specified tiers"""
        return [vc for vc in self.vcs.values() if vc.tier in tiers]

    def get_tier_1_vcs(self) -> List[VCFund]:
        """Get all Tier 1 VCs (top, upper, specialized)"""
        tier_1_levels = [
            VCTierLevel.TIER_1_TOP,
            VCTierLevel.TIER_1_UPPER,
            VCTierLevel.TIER_1_SPECIALIZED
        ]
        return self.filter_by_tiers(tier_1_levels)

    def get_predatory_vcs(self) -> List[VCFund]:
        """Get all predatory VCs"""
        return self.filter_by_tier(VCTierLevel.PREDATORY)

    def get_tier_counts(self) -> Dict[str, int]:
        """Get count of VCs per tier"""
        counts = {}
        for tier in VCTierLevel:
            counts[tier.value] = len([vc for vc in self.vcs.values() if vc.tier == tier])
        return counts

    def search_by_investment(self, token_name: str) -> List[VCFund]:
        """Find VCs that have invested in a specific token"""
        token_lower = token_name.lower()
        return [
            vc for vc in self.vcs.values()
            if any(token_lower in inv.lower() for inv in vc.key_investments)
        ]

    def classify_investor_list(self, investors: List[str]) -> Dict:
        """
        Classify a list of investors and return analysis

        Returns:
            {
                'tier_1_count': int,
                'tier_1_vcs': List[str],
                'tier_2_count': int,
                'predatory_count': int,
                'predatory_vcs': List[str],
                'conviction_score': float,  # 0-10
                'dump_risk': str,  # LOW/MEDIUM/HIGH/CRITICAL
                'total_modifier': float
            }
        """
        tier_1_vcs = []
        tier_2_vcs = []
        tier_3_vcs = []
        predatory_vcs = []
        unknown = []
        total_modifier = 0.0

        for investor in investors:
            vc = self.get_vc(investor)
            if vc:
                total_modifier += vc.conviction_modifier
                if vc.tier in [VCTierLevel.TIER_1_TOP, VCTierLevel.TIER_1_UPPER, VCTierLevel.TIER_1_SPECIALIZED]:
                    tier_1_vcs.append(vc.name)
                elif vc.tier == VCTierLevel.TIER_2:
                    tier_2_vcs.append(vc.name)
                elif vc.tier == VCTierLevel.TIER_3:
                    tier_3_vcs.append(vc.name)
                elif vc.tier == VCTierLevel.PREDATORY:
                    predatory_vcs.append(vc.name)
            else:
                unknown.append(investor)

        # Calculate conviction score (0-10)
        conviction_score = 0
        if len(tier_1_vcs) >= 3:
            conviction_score = 10
        elif len(tier_1_vcs) == 2:
            conviction_score = 8
        elif len(tier_1_vcs) == 1:
            conviction_score = 7
        elif len(tier_2_vcs) >= 2:
            conviction_score = 5
        elif len(tier_2_vcs) == 1 or len(tier_3_vcs) >= 2:
            conviction_score = 3
        elif unknown:
            conviction_score = 1

        # Reduce score if predatory VCs present
        if predatory_vcs:
            conviction_score = max(0, conviction_score - 2)

        # Determine dump risk
        dump_risk = "LOW"
        if predatory_vcs:
            dump_risk = "CRITICAL"
        elif len(tier_1_vcs) >= 2:
            dump_risk = "HIGH"  # More VCs = more dump pressure
        elif len(tier_1_vcs) == 1:
            dump_risk = "MEDIUM"

        return {
            'tier_1_count': len(tier_1_vcs),
            'tier_1_vcs': tier_1_vcs,
            'tier_2_count': len(tier_2_vcs),
            'tier_2_vcs': tier_2_vcs,
            'tier_3_count': len(tier_3_vcs),
            'predatory_count': len(predatory_vcs),
            'predatory_vcs': predatory_vcs,
            'unknown_investors': unknown,
            'conviction_score': conviction_score,
            'dump_risk': dump_risk,
            'total_modifier': total_modifier
        }

    def get_all_vc_names(self) -> List[str]:
        """Get list of all VC names"""
        return list(self.vcs.keys())

    def get_vc_summary(self) -> str:
        """Get summary statistics of the database"""
        counts = self.get_tier_counts()
        return (
            f"VC Database Summary:\n"
            f"  Total VCs: {len(self.vcs)}\n"
            f"  Tier 1 Top: {counts.get('tier_1_top', 0)}\n"
            f"  Tier 1 Upper: {counts.get('tier_1_upper', 0)}\n"
            f"  Tier 1 Specialized: {counts.get('tier_1_specialized', 0)}\n"
            f"  Tier 2: {counts.get('tier_2', 0)}\n"
            f"  Tier 3: {counts.get('tier_3', 0)}\n"
            f"  Predatory: {counts.get('predatory', 0)}"
        )


# Singleton instance
_vc_database: Optional[VCDatabase] = None


def get_vc_database() -> VCDatabase:
    """Get singleton VC database instance"""
    global _vc_database
    if _vc_database is None:
        _vc_database = VCDatabase()
    return _vc_database


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    db = get_vc_database()
    print(db.get_vc_summary())

    print("\n--- Tier 1 Top VCs ---")
    for vc in db.filter_by_tier(VCTierLevel.TIER_1_TOP):
        print(f"  {vc.name}: {vc.aum_usd or 'N/A'}")

    print("\n--- Test Classification ---")
    test_investors = ["Paradigm", "Dragonfly Capital", "DWF Labs", "Unknown VC"]
    result = db.classify_investor_list(test_investors)
    print(f"  Tier 1 VCs: {result['tier_1_vcs']}")
    print(f"  Predatory: {result['predatory_vcs']}")
    print(f"  Conviction Score: {result['conviction_score']}/10")
    print(f"  Dump Risk: {result['dump_risk']}")
