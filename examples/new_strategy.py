"""Interface demonstration only. Not a recommended trading strategy.

Upload this trusted file in 策略工厂, class name ExampleStrategy.
Register different lookback values as separate research versions.
"""


class ExampleStrategy:
    def __init__(self, lookback=20):
        if not isinstance(lookback, int) or not 2 <= lookback <= 100:
            raise ValueError('lookback must be an integer from 2 to 100')
        self.lookback = lookback

    @classmethod
    def get_metadata(cls):
        return {'display_name':'接口示例：突破观察','signal_column':'example_signal',
                'entry_column':'example_entry','sl_column':'example_stop',
                'tp_columns':['example_target'],'score_column':'example_score',
                'supported_timeframes':['daily']}

    def calculate_signals(self, frame):
        df = frame.copy()
        previous = df.high.rolling(self.lookback).max().shift(1)
        df['example_signal'] = df.close > previous
        df['example_entry'] = df.high + 0.01
        df['example_stop'] = df.low
        df['example_target'] = df.example_entry + 2*(df.example_entry-df.example_stop)
        df['example_score'] = 1.0
        return df
