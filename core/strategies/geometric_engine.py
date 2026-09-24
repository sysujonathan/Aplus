import pandas as pd
import numpy as np
from typing import Tuple, Optional, List
from dataclasses import dataclass

@dataclass
class SwingPoint:
    """Represents a significant swing point in price"""
    idx: int           # Bar index (integer index)
    price: float       # Price level
    type: str          # 'high' or 'low'
    strength: float    # Relative strength (higher = more significant)

class GeometricTrendlineEngine:
    """
    Implements Al Brooks-style geometric trendlines using swing points.
    Handles bear trendlines by connecting lower highs.
    """
    
    def __init__(self, swing_window: int = 5, atr_window: int = 14, 
                 min_bar_distance: int = 3, break_threshold: float = 0.5):
        """
        Args:
            swing_window: Bars to look left/right for swing points (Fractal logic).
            atr_window: Period for ATR calculation if not provided.
            min_bar_distance: Minimum bars between significant swing points.
            break_threshold: Multiple of ATR for significant breakout.
        """
        self.swing_window = swing_window
        self.atr_window = atr_window
        self.min_bar_distance = min_bar_distance
        self.break_threshold = break_threshold
        
    def find_swing_points(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        """
        Identify significant swing highs and lows using fractal method.
        
        Args:
            df: DataFrame with 'high', 'low', 'close' columns
            
        Returns:
            Tuple of (swing_highs, swing_lows) as boolean Series
        """
        # Initialize with False (Length aligned with Reset Index)
        swing_highs = pd.Series([False] * len(df), dtype=bool)
        swing_lows = pd.Series([False] * len(df), dtype=bool)
        
        # Look for swing highs (local maxima)
        # Using vectorized comparison is hard for variable window, sticking to loops for clarity within reasonable bounds
        # Or optimization: use rolling checks
        
        # Optimized implementation using rolling
        # Check if current high is max in window [i-window, i+window]
        # But rolling is centered?
        
        # Simple loop is fine for daily data (~1000 bars)
        for i in range(self.swing_window, len(df) - self.swing_window):
            window_high = df['high'].iloc[i]
            
            # Check left side
            left_valid = all(window_high > df['high'].iloc[i-j] 
                           for j in range(1, self.swing_window + 1))
            
            # Check right side
            right_valid = all(window_high > df['high'].iloc[i+j] 
                            for j in range(1, self.swing_window + 1))
            
            if left_valid and right_valid:
                swing_highs.iloc[i] = True
                
        # Look for swing lows (local minima)
        for i in range(self.swing_window, len(df) - self.swing_window):
            window_low = df['low'].iloc[i]
            
            # Check left side
            left_valid = all(window_low < df['low'].iloc[i-j] 
                           for j in range(1, self.swing_window + 1))
            
            # Check right side
            right_valid = all(window_low < df['low'].iloc[i+j] 
                            for j in range(1, self.swing_window + 1))
            
            if left_valid and right_valid:
                swing_lows.iloc[i] = True
                
        # Filter: require minimum distance between significant swings
        swing_highs = self._filter_swing_distance(df, swing_highs, 'high')
        swing_lows = self._filter_swing_distance(df, swing_lows, 'low')
        
        return swing_highs, swing_lows
    
    def _filter_swing_distance(self, df: pd.DataFrame, 
                              swings: pd.Series, 
                              price_type: str) -> pd.Series:
        """Ensure minimum distance between swing points"""
        filtered = swings.copy()
        # Ensure we work with integer indices
        df_reset = df.reset_index(drop=True) 
        swing_indices = df_reset.index[filtered.values].tolist()
        
        if not swing_indices:
            return filtered
            
        price_col = 'high' if price_type == 'high' else 'low'
        
        # Keep only the most significant swings within min_bar_distance
        i = 0
        while i < len(swing_indices) - 1:
            j = i + 1
            while j < len(swing_indices):
                idx1, idx2 = swing_indices[i], swing_indices[j]
                bar_diff = abs(idx2 - idx1)
                
                if bar_diff < self.min_bar_distance:
                    # Keep the higher high or lower low
                    price1 = df_reset.loc[idx1, price_col]
                    price2 = df_reset.loc[idx2, price_col]
                    
                    if price_type == 'high':
                        # Keep the higher high
                        remove_idx = idx1 if price1 < price2 else idx2
                        keep_idx = idx2 if price1 < price2 else idx1
                    else:
                        # Keep the lower low
                        remove_idx = idx1 if price1 > price2 else idx2
                        keep_idx = idx2 if price1 > price2 else idx1
                    
                    # Map back to original index label for filtering
                    # Actually filter by integer location
                    filtered.iloc[remove_idx] = False
                    
                    # Update list
                    # Re-fetch is safer or manual update
                    if remove_idx == idx1:
                        # idx1 removed, check idx2 against next
                        swing_indices[i] = idx2
                        swing_indices.pop(j)
                        # Don't increment j, check new j against current i (which is now idx2)
                        # Wait, logic:
                        # current i is idx1. if idx1 removed, we need to re-evaluate at i (now idx2)
                        # but idx2 is at j.
                        # Easier: just invalidate and continue
                        pass
                    else:
                        # idx2 removed
                        swing_indices.pop(j)
                        # Don't increment j, check next candidate against idx1
                        continue 
                else:
                    j += 1
            i += 1
            
        return filtered
    
    def identify_swing_objects(self, df: pd.DataFrame, 
                              swing_highs: pd.Series, 
                              swing_lows: pd.Series) -> List[SwingPoint]:
        """Convert swing points to SwingPoint objects with strength calculation"""
        swing_points = []
        
        # Calculate average bar range for strength normalization
        avg_range = (df['high'] - df['low']).rolling(20).mean().median()
        if pd.isna(avg_range) or avg_range == 0:
            avg_range = 1.0 # fallback
        
        # Iterate over swing highs
        # Note: swing_highs is boolean series on original index
        for idx in df.index[swing_highs]:
            i = df.index.get_loc(idx)
            if i > 0 and i < len(df) - 1:
                # Strength based on price move from previous low to this high
                lookback = min(10, i)
                prev_low = df['low'].iloc[i-lookback:i].min()
                price_move = df.loc[idx, 'high'] - prev_low
                strength = price_move / avg_range
                
                swing_points.append(
                    SwingPoint(idx=idx, price=df.loc[idx, 'high'], 
                              type='high', strength=strength)
                )
                
        for idx in df.index[swing_lows]:
            i = df.index.get_loc(idx)
            if i > 0 and i < len(df) - 1:
                # Strength based on price move from previous high to this low
                lookback = min(10, i)
                prev_high = df['high'].iloc[i-lookback:i].max()
                price_move = prev_high - df.loc[idx, 'low']
                strength = price_move / avg_range
                
                swing_points.append(
                    SwingPoint(idx=idx, price=df.loc[idx, 'low'], 
                              type='low', strength=strength)
                )
                
        # Sort by index
        # Need to handle potential non-comparable index if not datetime. Assuming datetime is sortable.
        # But SwingPoint.idx stores the LABEL (timestamp).
        
        # Issue: SwingPoint.idx in original code was Int. 
        # Here I stored label. Let's fix.
        # DeepSeek code used `idx` as label in `identify_swing_objects` but treated it as label later.
        # `df.index.get_loc` handles label->int.
        
        # Actually, let's store both or rely on label. 
        # DeepSeek code: `idx: int # Bar index` in dataclass, but passed `df.loc[idx]` which implies label.
        # Let's adjust to store Label as `idx` (timestamp) for compatibility with pandas loc.
        
        # Sort using the Label (Timestamp)
        swing_points.sort(key=lambda x: x.idx)
        return swing_points
    
    def find_bear_trendline(self, df: pd.DataFrame, 
                           swing_points: List[SwingPoint],
                           current_bar_label) -> Optional[Tuple[float, float, any, any]]:
        """
        Find the best bear trendline (connecting lower highs).
        
        Args:
            current_bar_label: The index label of the current bar
            
        Returns:
            Tuple of (slope, intercept, start_label, end_label) or None
        """
        # 🟢 [Optimization] Direct int index (Scanner reset ensures RangeIndex)
        current_loc = current_bar_label
        
        # Get only swing highs up to current bar (Assume RangeIndex)
        swing_highs = [sp for sp in swing_points 
                      if sp.type == 'high' and sp.idx <= current_loc]
        
        if len(swing_highs) < 2:
            return None
            
        # Try different combinations to find the best trendline
        best_trendline = None
        best_score = -np.inf
        
        # Limit to recent significant highs to avoid N^2 on entire history
        recent_highs = sorted(swing_highs, key=lambda x: x.idx)[-10:] # Last 10 highs
        
        for i in range(len(recent_highs) - 1):
            for j in range(i + 1, len(recent_highs)):
                point1 = recent_highs[i]
                point2 = recent_highs[j]
                
                # Must be lower highs (bearish) structure generally, but Al Brooks allows some overshoot.
                # Strictly: point2 < point1 for classic bear trendline.
                # But allow slight upward slope if channel is broad? No, bear trendline should slope down.
                if point2.price >= point1.price:
                     # Allow slight error or flat? Strict Lower Highs for Major Bear Trend.
                     # If equal, it's a double top.
                     if point2.price > point1.price * 1.01: # 1% tolerance
                        continue
                    
                # Calculate trendline
                x1 = point1.idx
                x2 = point2.idx
                y1 = point1.price
                y2 = point2.price
                
                if x2 == x1: continue
                    
                slope = (y2 - y1) / (x2 - x1)
                intercept = y1 - slope * x1
                
                # Score this trendline
                score = self._score_trendline(df, recent_highs, slope, intercept, 
                                            point1.idx, point2.idx, current_loc)
                
                if score > best_score:
                    best_score = score
                    best_trendline = (slope, intercept, point1.idx, point2.idx)
        
        return best_trendline
    
    def _score_trendline(self, df: pd.DataFrame, 
                        swing_highs: List[SwingPoint], 
                        slope: float, intercept: float,
                        idx1_label, idx2_label, current_loc: int) -> float:
        """Score a trendline based on touches, containment, and recency"""
        score = 0.0
        
        # 🟢 [Optimized] RangeIndex assumption
        x1 = idx1_label
        x2 = idx2_label
        
        # 🟢 1. Recency Bias: Favor lines defined by recent points
        recency_factor = max(0, 1.0 - (current_loc - x2) / 60.0)
        score += recency_factor * 20.0
        
        # 🟢 2. Density: Favor points between x1 and x2 that are close to the line
        touches = 0
        for sp in swing_highs:
            sx = sp.idx
            if sx < x1: continue # Don't care about past
            
            line_val = slope * sx + intercept
            
            # Normalize distance by ATR
            atr = df.at[sx, 'atr'] if 'atr' in df.columns else (sp.price * 0.01)
            dist = abs(sp.price - line_val) / atr
            
            if sp.price > line_val + 0.2 * atr:
                score -= 10.0 * (dist ** 2)
            elif dist < 0.5:
                touches += 1
                score += 5.0 * (1.0 - dist)
                
        # 🟢 3. Multi-point bonus
        score += touches * 2.0
        
        return score
    
    def calculate_trendline_value(self, df: pd.DataFrame, 
                                 trendline: Tuple[float, float, any, any], 
                                 target_idx_label) -> float:
        """Calculate trendline value at a specific bar index label."""
        slope, intercept, _, _ = trendline
        x = target_idx_label # Assumes RangeIndex
        return slope * x + intercept
    
    def check_trendline_break(self, df: pd.DataFrame, 
                             current_idx_label,
                             trendline: Tuple[float, float, any, any]) -> bool:
        """
        Check for valid trendline breakout.
        """
        if trendline is None:
            return False
            
        slope, intercept, _, trendline_end_label = trendline
        
        # Must be AFTER the last defining point of the line
        if current_idx_label <= trendline_end_label:
            return False
            
        current_close = df.at[current_idx_label, 'close']
        trendline_val = self.calculate_trendline_value(df, trendline, current_idx_label)
        
        # Check if close is above trendline
        if current_close <= trendline_val:
            return False
            
        # Check for significant break (> 0.5 ATR typically)
        atr = df.at[current_idx_label, 'atr']
        if pd.isna(atr) or atr == 0: atr = current_close * 0.01
            
        break_dist = (current_close - trendline_val) / atr
        return break_dist >= self.break_threshold

    def analyze_bar(self, df: pd.DataFrame, bar_index_label) -> dict:
        """
        Complete analysis for a single bar.
        """
        # We need historical context. 
        # Identify swing points on the whole provided df up to this point?
        # Efficiently: Pre-calculate swings for the whole DF once?
        # But MTRStrategy typically calls this per bar or on slice.
        
        # Let's assume df is the context window (e.g. 100 bars) ending at bar_index_label
        # Or df is full history.
        
        # For simplicity in this engine: re-eval strokes.
        swing_highs, swing_lows = self.find_swing_points(df)
        swing_points = self.identify_swing_objects(df, swing_highs, swing_lows)
        
        trendline = self.find_bear_trendline(df, swing_points, bar_index_label)
        
        has_break = False
        trendline_val = None
        
        if trendline:
            has_break = self.check_trendline_break(df, bar_index_label, trendline)
            trendline_val = self.calculate_trendline_value(df, trendline, bar_index_label)
            
        return {
            'has_trendline': trendline is not None,
            'trendline_value': trendline_val,
            'has_break': has_break,
            'trendline_slope': trendline[0] if trendline else 0,
        }
