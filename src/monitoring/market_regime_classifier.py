"""
Market Regime Classifier

Classifies current market as BULL, BEAR, or CHOP based on:
1. BTC/ETH momentum (MA crossovers)
2. USDT Dominance trends
3. Volatility patterns

Used by playbook monitor to tag every condition check with regime context.

Rationale (Gemini Feedback):
"Trendline breaks work in BULL markets, fail in BEAR. Without regime tagging,
Agent 7's learning is poisoned by mixing contexts."

Author: DACLE System
Created: 2025-12-03
Session: 84 (Post-Gemini Feedback)
"""

import os
import sys
from datetime import datetime, timezone
from typing import Dict, Literal, Optional
import ccxt
import pandas as pd

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

MarketRegime = Literal['BULL', 'BEAR', 'CHOP']


class MarketRegimeClassifier:
    """
    Classifies market regime using BTC/ETH momentum and USDT.D trends.

    Classification Rules:

    BULL:
    - BTC > MA(20) AND BTC > MA(50)
    - ETH > MA(20) AND ETH > MA(50)
    - USDT.D declining (< MA(20))

    BEAR:
    - BTC < MA(20) AND BTC < MA(50)
    - ETH < MA(20) AND ETH < MA(50)
    - USDT.D rising (> MA(20))

    CHOP (default):
    - Mixed signals
    - Low conviction
    """

    def __init__(self):
        """Initialize with CCXT exchange client."""
        self.exchange = ccxt.binance({'enableRateLimit': True})

    def classify(self) -> Dict[str, any]:
        """
        Classify current market regime.

        Returns:
            Dict with:
                - regime: 'BULL', 'BEAR', or 'CHOP'
                - confidence: 0.0-1.0
                - signals: Dict with component signals
                - timestamp: Classification timestamp
        """
        try:
            # Fetch BTC and ETH data
            btc_signal = self._get_momentum_signal('BTC/USDT')
            eth_signal = self._get_momentum_signal('ETH/USDT')
            usdt_d_signal = self._get_usdt_dominance_signal()

            # Combine signals
            regime = self._determine_regime(btc_signal, eth_signal, usdt_d_signal)
            confidence = self._calculate_confidence(btc_signal, eth_signal, usdt_d_signal)

            return {
                'regime': regime,
                'confidence': confidence,
                'signals': {
                    'btc': btc_signal,
                    'eth': eth_signal,
                    'usdt_d': usdt_d_signal
                },
                'timestamp': datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            print(f"⚠️  Market regime classification failed: {e}")
            # Fallback to CHOP (conservative default)
            return {
                'regime': 'CHOP',
                'confidence': 0.5,
                'signals': {},
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'error': str(e)
            }

    def _get_momentum_signal(self, symbol: str) -> Dict[str, any]:
        """
        Get momentum signal for a symbol using MA crossovers.

        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')

        Returns:
            Dict with:
                - direction: 'UP', 'DOWN', or 'NEUTRAL'
                - strength: 0.0-1.0
                - price: Current price
                - ma20: 20-period MA
                - ma50: 50-period MA
        """
        try:
            # Fetch OHLCV data (4h timeframe, 60 candles)
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe='4h', limit=60)

            if not ohlcv or len(ohlcv) < 50:
                return {'direction': 'NEUTRAL', 'strength': 0.5}

            # Convert to DataFrame
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            # Calculate MAs
            df['ma20'] = df['close'].rolling(window=20).mean()
            df['ma50'] = df['close'].rolling(window=50).mean()

            # Get latest values
            current_price = df['close'].iloc[-1]
            ma20 = df['ma20'].iloc[-1]
            ma50 = df['ma50'].iloc[-1]

            # Determine direction
            if current_price > ma20 and current_price > ma50 and ma20 > ma50:
                # Strong uptrend
                direction = 'UP'
                # Strength based on distance from MAs
                strength = min(1.0, (current_price - ma50) / ma50 * 10)  # Normalize to 0-1
            elif current_price < ma20 and current_price < ma50 and ma20 < ma50:
                # Strong downtrend
                direction = 'DOWN'
                strength = min(1.0, (ma50 - current_price) / ma50 * 10)
            else:
                # Mixed signals
                direction = 'NEUTRAL'
                strength = 0.5

            return {
                'direction': direction,
                'strength': round(strength, 2),
                'price': round(current_price, 2),
                'ma20': round(ma20, 2),
                'ma50': round(ma50, 2)
            }

        except Exception as e:
            print(f"⚠️  Failed to get momentum signal for {symbol}: {e}")
            return {'direction': 'NEUTRAL', 'strength': 0.5}

    def _get_usdt_dominance_signal(self) -> Dict[str, any]:
        """
        Get USDT dominance signal.

        Note: USDT.D not directly available on Binance.
        We'll use USDT volume as proxy (higher volume = higher dominance = risk-off).

        Returns:
            Dict with:
                - trend: 'RISING', 'FALLING', or 'FLAT'
                - strength: 0.0-1.0
        """
        try:
            # Fetch BTC/USDT volume as proxy
            ohlcv = self.exchange.fetch_ohlcv('BTC/USDT', timeframe='1d', limit=30)

            if not ohlcv or len(ohlcv) < 20:
                return {'trend': 'FLAT', 'strength': 0.5}

            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            # Calculate volume MA
            df['volume_ma20'] = df['volume'].rolling(window=20).mean()

            current_volume = df['volume'].iloc[-1]
            volume_ma20 = df['volume_ma20'].iloc[-1]

            # Rising volume = risk-off (bearish)
            # Falling volume = risk-on (bullish)
            if current_volume > volume_ma20 * 1.2:
                trend = 'RISING'
                strength = 0.8
            elif current_volume < volume_ma20 * 0.8:
                trend = 'FALLING'
                strength = 0.8
            else:
                trend = 'FLAT'
                strength = 0.5

            return {
                'trend': trend,
                'strength': round(strength, 2)
            }

        except Exception as e:
            print(f"⚠️  Failed to get USDT dominance signal: {e}")
            return {'trend': 'FLAT', 'strength': 0.5}

    def _determine_regime(
        self,
        btc_signal: Dict,
        eth_signal: Dict,
        usdt_d_signal: Dict
    ) -> MarketRegime:
        """
        Determine market regime from component signals.

        Args:
            btc_signal: BTC momentum signal
            eth_signal: ETH momentum signal
            usdt_d_signal: USDT dominance signal

        Returns:
            'BULL', 'BEAR', or 'CHOP'
        """
        btc_direction = btc_signal.get('direction', 'NEUTRAL')
        eth_direction = eth_signal.get('direction', 'NEUTRAL')
        usdt_trend = usdt_d_signal.get('trend', 'FLAT')

        # BULL: Both BTC and ETH uptrending, USDT falling
        if btc_direction == 'UP' and eth_direction == 'UP':
            if usdt_trend == 'FALLING':
                return 'BULL'
            else:
                # BTC/ETH up but USDT not falling = weak bull
                return 'CHOP'

        # BEAR: Both BTC and ETH downtrending, USDT rising
        elif btc_direction == 'DOWN' and eth_direction == 'DOWN':
            if usdt_trend == 'RISING':
                return 'BEAR'
            else:
                # BTC/ETH down but USDT not rising = weak bear
                return 'CHOP'

        # CHOP: Mixed or neutral signals
        else:
            return 'CHOP'

    def _calculate_confidence(
        self,
        btc_signal: Dict,
        eth_signal: Dict,
        usdt_d_signal: Dict
    ) -> float:
        """
        Calculate confidence in regime classification.

        Args:
            btc_signal: BTC momentum signal
            eth_signal: ETH momentum signal
            usdt_d_signal: USDT dominance signal

        Returns:
            Confidence score 0.0-1.0
        """
        # Average strength of all signals
        btc_strength = btc_signal.get('strength', 0.5)
        eth_strength = eth_signal.get('strength', 0.5)
        usdt_strength = usdt_d_signal.get('strength', 0.5)

        # Weighted average (BTC 40%, ETH 30%, USDT 30%)
        confidence = (btc_strength * 0.4) + (eth_strength * 0.3) + (usdt_strength * 0.3)

        return round(confidence, 2)


# CLI for testing
def main():
    """Test market regime classifier."""
    print("\n" + "="*60)
    print("🔍 Market Regime Classifier Test")
    print("="*60 + "\n")

    classifier = MarketRegimeClassifier()
    result = classifier.classify()

    print(f"Regime: {result['regime']}")
    print(f"Confidence: {result['confidence']:.2%}")
    print(f"\nSignals:")
    print(f"  BTC: {result['signals'].get('btc', {})}")
    print(f"  ETH: {result['signals'].get('eth', {})}")
    print(f"  USDT.D: {result['signals'].get('usdt_d', {})}")
    print(f"\nTimestamp: {result['timestamp']}")
    print("\n" + "="*60 + "\n")


if __name__ == '__main__':
    main()
