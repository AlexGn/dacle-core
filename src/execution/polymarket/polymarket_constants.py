"""
Canonical constants for Polymarket execution.
Centralizing these prevents partial-migration incidents where the SDK and
Direct-On-Chain paths use different contract versions.
"""

# V1 Constants (Current) - To be replaced by V2 values during migration
CTF_EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
COLLATERAL_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e
COLLATERAL_DECIMALS = 6

# V2 Constants (Planned)
# To be populated from Track A intelligence gathering
# V2_CTF_EXCHANGE_ADDRESS = "TBD"
# V2_COLLATERAL_ADDRESS = "TBD" # PolyUSD
# V2_COLLATERAL_DECIMALS = 6 # or 18 TBD
# V2_COLLATERAL_ONRAMP_ADDRESS = "0x3A3BD7bb9528E159577F7C2e685CC81A765002E2"

