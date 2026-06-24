from typing import Dict

# Symbol -> Internal Sector Key (e.g., 'AI.C')
SYMBOL_TO_SECTOR: Dict[str, str] = {
    # Meme
    "PEPE": "MEME.C",
    "DOGE": "MEME.C",
    "SHIB": "MEME.C",
    "WIF": "MEME.C",
    "BONK": "MEME.C",
    "FLOKI": "MEME.C",
    "MEME": "MEME.C",
    "TURBO": "MEME.C",
    "BOME": "MEME.C",
    "BRETT": "MEME.C",
    "POPCAT": "MEME.C",
    "PNUT": "MEME.C",
    "NEIRO": "MEME.C",
    "1000SATS": "MEME.C",
    "1000BONK": "MEME.C",
    "1000FLOKI": "MEME.C",
    "1000LUNC": "MEME.C",
    "1000RATS": "MEME.C",
    "MOODENG": "MEME.C",
    "GOAT": "MEME.C",
    "TRUMP": "MEME.C",
    "PENGU": "MEME.C",
    "SPX": "MEME.C",
    # AI
    "FET": "AI.C",
    "AGIX": "AI.C",
    "RENDER": "AI.C",
    "TAO": "AI.C",
    "OLAS": "AI.C",
    "NMR": "AI.C",
    "OCEAN": "AI.C",
    "AIA": "AI.C",
    "AIXBT": "AI.C",
    "CGPT": "AI.C",
    "AIGENSYN": "AI.C",
    # Layer 1
    "SOL": "LAYER1.C",
    "AVAX": "LAYER1.C",
    "ADA": "LAYER1.C",
    "DOT": "LAYER1.C",
    "NEAR": "LAYER1.C",
    "APT": "LAYER1.C",
    "SUI": "LAYER1.C",
    "TON": "LAYER1.C",
    "SAGA": "LAYER1.C",
    "SEI": "LAYER1.C",
    "EIGEN": "LAYER1.C",
    "TIA": "LAYER1.C",
    "AAVE": "LAYER1.C",
    "LDO": "LAYER1.C",
    # DePIN
    "HNT": "DEPIN.C",
    "MOBILE": "DEPIN.C",
    "IOT": "DEPIN.C",
    "RNDR": "DEPIN.C",
    "AIOZ": "DEPIN.C",
    "FIL": "DEPIN.C",
    "AR": "DEPIN.C",
    "GRT": "DEPIN.C",
    "AKT": "DEPIN.C",
    "ATH": "DEPIN.C",
    "ICP": "DEPIN.C",
    "GRASS": "DEPIN.C",
    "THETA": "DEPIN.C",
    "JASMY": "DEPIN.C",
    "XYO": "DEPIN.C",
    "IOTA": "DEPIN.C",
    # RWA
    "ONDO": "RWA.C",
    "POLYX": "RWA.C",
    "CFG": "RWA.C",
    "MPL": "RWA.C",
    "MKR": "RWA.C",
    "PENDLE": "RWA.C",
    # Solana ecosystem
    "JUP": "SOLANA.C",
    "JTO": "SOLANA.C",
    "PYTH": "SOLANA.C",
    "RAY": "SOLANA.C",
    "DRIFT": "SOLANA.C",
    "MNGO": "SOLANA.C",
    "KMNO": "SOLANA.C",
    # TradFi / Equities
    "AAPL": "TRADFI.C",
    "AMD": "TRADFI.C",
    "AMZN": "TRADFI.C",
    "ARM": "TRADFI.C",
    "AVGO": "TRADFI.C",
    "B": "TRADFI.C",
    "COHR": "TRADFI.C",
    "DELL": "TRADFI.C",
    "DIS": "TRADFI.C",
    "GOOGL": "TRADFI.C",
    "H": "TRADFI.C",
    "IBM": "TRADFI.C",
    "INTC": "TRADFI.C",
    "JPM": "TRADFI.C",
    "LITE": "TRADFI.C",
    "LLY": "TRADFI.C",
    "META": "TRADFI.C",
    "MSFT": "TRADFI.C",
    "MSTR": "TRADFI.C",
    "MU": "TRADFI.C",
    "NVDA": "TRADFI.C",
    "ORCL": "TRADFI.C",
    "S": "TRADFI.C",
    "SNDK": "TRADFI.C",
    "T": "TRADFI.C",
    "TSLA": "TRADFI.C",
    "TSM": "TRADFI.C",
    "UBER": "TRADFI.C",
    "V": "TRADFI.C",
    "W": "TRADFI.C",
    "XAU": "TRADFI.C",
    "XAG": "TRADFI.C",
    # Specifically filtered out sectors
    "OPN": "PREDICTION.C",
}

# Internal Key -> Human Readable Label
SECTOR_LABELS: Dict[str, str] = {
    "MEME.C": "Meme",
    "AI.C": "AI",
    "LAYER1.C": "Layer 1",
    "DEPIN.C": "Depin",
    "RWA.C": "RWA",
    "SOLANA.C": "Solana Ecosystem",
    "PREDICTION.C": "Prediction",
    "TRADFI.C": "TradFi / Equities",
}

FOCUSED_SECTORS = {"Meme", "AI", "Layer 1", "Depin", "RWA", "Solana Ecosystem"}


def get_sector_label(symbol: str) -> str:
    """Returns the human-readable label for a symbol, or 'Unknown' if not found."""
    sector_key = SYMBOL_TO_SECTOR.get(symbol.upper(), "")
    return SECTOR_LABELS.get(sector_key, "Unknown")


def is_focused_sector(symbol: str) -> bool:
    """Checks if a symbol belongs to a sector we want to highlight in reports."""
    label = get_sector_label(symbol)
    return label in FOCUSED_SECTORS
