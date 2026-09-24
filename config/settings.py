import os
from dotenv import load_dotenv

# 加载 .env 环境变量
load_dotenv()

# ================= 基础路径配置 =================
# 获取项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 数据库路径
DB_PATH = os.path.join(BASE_DIR, 'data', 'baostock.db')  # 🟢 V8.13 新库

# 字体路径
FONT_PATH = os.path.join(BASE_DIR, 'config', 'fonts', 'SimHei.ttf')

# 报告/日志输出目录 (这就是报错缺失的变量)
REPORT_DIR = os.path.join(BASE_DIR, 'reports')

# ================= API 与通知配置 =================
# DeepSeek 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-reasoner")
API_URL = "https://api.deepseek.com/chat/completions"

# 微信推送配置
WECHAT_WEBHOOK_KEY = os.getenv("WECHAT_WEBHOOK_KEY")

# Discord 推送配置
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_SERVER_ID = os.getenv("DISCORD_SERVER_ID")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "1478561953038336093")

# ================= 数据源配置 =================
# 🟢 V8.13: Baostock Only (不再使用 Tushare/AkShare)
# TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")  # Reverted (User Request: Stabilize)
DATA_SOURCE_PRIORITY = ["baostock"]  # Single Source

# ================= 系统运行参数 =================
# 扫描并发进程数
# 🟢 Baostock 服务端对同一 IP 的并发 TCP 连接硬限制为 5
# 实证：6 Worker 时第 6 个连接即使重试 3 次也必定超时 (2026-07 连续验证)
# 5 Worker 与 6 Worker(1死) 有效吞吐量相同，但 5 Worker 零报错、一次完成
MAX_WORKERS = int(os.getenv("MAX_WORKERS", 5))

# ================= 数据源限流配置（防夜间高峰被 Baostock 黑名单） =================
# 分时段限流开关：True=开启（夜间高峰时段对日线请求加随机停顿，模拟真人节奏，降低被封概率）
BAOSTOCK_RATE_LIMIT_ENABLED = True
# 高峰时段窗口（本地时间，小时，左闭右开）：默认 18:00-24:00（收盘后数据归档时段）
BAOSTOCK_RATE_LIMIT_START_HOUR = 18
BAOSTOCK_RATE_LIMIT_END_HOUR = 24
# 高峰时段内、每只股票请求前的随机停顿区间（秒）
BAOSTOCK_RATE_LIMIT_DELAY_MIN = 0.2
BAOSTOCK_RATE_LIMIT_DELAY_MAX = 0.5

# 长图拼接数量
MAX_IMAGES_PER_BATCH = int(os.getenv("MAX_IMAGES_PER_BATCH", 10))

# 图表选图 (每策略 TOP-N): 每个被发现的策略最多出图张数, 超出部分聚合为文字摘要。
# 不设"全局总张数"上限 —— 目的是让每种策略都能看到自己的 TOP-N (如 MTR 65只取10, AWIL 5只全出),
# 避免大策略(批量命中)把小策略整批挤出图表。总张数 = Σ min(命中数, 本值)。
MAX_CHARTS_PER_STRATEGY = int(os.getenv("MAX_CHARTS_PER_STRATEGY", 10))

# ================= 策略参数配置 =================
# 通用
STRATEGY_MIN_DATA_LENGTH = 125  # Increased for MTR V35 safety
STRATEGY_DATA_FETCH_LIMIT = 300 # 🟢 Upgraded for MTR V35 (Needs 120 lookback)

# V7 Strategy
V7_ADX_STR_THRESHOLD = 15.0
V7_WICK_RATIO = 0.40
V7_CLOSE_POS_RATIO = 0.80
V7_EMA_SLOPE_THRESHOLD = -0.01
V7_EMA_DIST_THRESHOLD = 0.02
V7_SL_ATR_MULTIPLIER = 3.0

# 3K Strategy
K3_BODY_PCT_A = 0.50
K3_WICK_LONG_PCT = 0.33
K3_WICK_SHORT_PCT = 0.10
K3_GAP_CONFIRM_WINDOW = 5  # 突破缺口确认窗口（3K后N根K线确认缺口是否保持开放）
K3_GAP_TEST_MAX_WINDOW = 20  # 缺口测试确认最大等待窗口（3K后最多等N根K线确认买入）
K3_SWING_LOOKBACK = 40  # 波段高低点回溯窗口（向前看N根K线找前期波段高/低点）

# Structural Gap Strategy (V3.0 宏观结构突破缺口)
STRUCT_GAP_LOOKBACK = 60  # 突破判定窗口: 过去 N 根 K 线的最高点 (日线≈3个月, 周线≈1.2年)
STRUCT_GAP_MAX_WINDOW = 40  # 回调确认最大跟踪窗口 (突破后最多等 N 根 K 线出现反转信号)

# MTR Strategy
MTR_WICK_RATIO = 0.25 # Relaxed from 0.40 (Digital Abu Consensus)
MTR_CLOSE_POS_RATIO = 0.50 # Relaxed from 0.80 (Top Half)

# ================= 数据质量 / 鲁棒性 =================
# 停牌边界保护 (P1④b): 相邻日线 bar 日历间隔超过此天数, 视为"停牌后复牌",
# 该 bar 与前一根(数周前)旧 bar 之间的"缺口"判定为恢复性缺口而非突破缺口,
# 不计入 has_gap / gap_upside / gap_down_count_20。
# 默认 10 天: 与实测"缺口>10天=停牌"对齐, 同时避免误伤长假(春节/国庆≈9天)后真实跳空。
SUSPENSION_RESUME_DAYS = int(os.getenv("SUSPENSION_RESUME_DAYS", 10))

# ================= 数据库与数据配置 =================
DB_POOL_SIZE = 10
DB_TIMEOUT = 60
DATA_FETCH_MINUTE_LIMIT = 500
DEFAULT_MINUTE_LIMIT = 200
ENABLE_SNAPSHOT_CACHE = True
