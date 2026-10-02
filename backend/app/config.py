"""\n配置管理\n统一从项目根目录的 .env 文件加载配置\n"""

import os
import sys
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件
# 路径: MiroFish/.env (相对于 backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.environ.get('MIROFISH_SIMULATION_WORKER') != '1':
    if os.path.exists(project_root_env):
        load_dotenv(project_root_env, override=False)
    else:
        load_dotenv(override=False)


class Config:
    """Flask配置类"""
    
    # Flask配置
    SECRET_KEY = os.environ.get('SECRET_KEY')
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    MIROFISH_ACCESS_KEY = os.environ.get('MIROFISH_ACCESS_KEY', '')
    MIROFISH_ALLOWED_ORIGINS = os.environ.get(
        'MIROFISH_ALLOWED_ORIGINS',
        'http://localhost:3000,http://127.0.0.1:3000,http://localhost:5001,http://127.0.0.1:5001',
    ).split(',')
    MIROFISH_COOKIE_SECURE = os.environ.get('MIROFISH_COOKIE_SECURE', 'false').lower() == 'true'
    
    # JSON配置 - 禁用ASCII转义，让中文直接显示
    JSON_AS_ASCII = False
    
    # LLM配置（统一使用OpenAI格式）
    LLM_PROVIDER = os.environ.get('LLM_PROVIDER', 'openai')
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    OLLAMA_API_KEY = os.environ.get('OLLAMA_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME')
    LLM_TOKEN_LIMIT = os.environ.get('LLM_TOKEN_LIMIT', '32768')
    LLM_BOOST_PROVIDER = os.environ.get('LLM_BOOST_PROVIDER')
    LLM_BOOST_API_KEY = os.environ.get('LLM_BOOST_API_KEY')
    LLM_BOOST_BASE_URL = os.environ.get('LLM_BOOST_BASE_URL')
    LLM_BOOST_MODEL_NAME = os.environ.get('LLM_BOOST_MODEL_NAME')
    LLM_BOOST_TOKEN_LIMIT = os.environ.get('LLM_BOOST_TOKEN_LIMIT')
    
    # Zep配置
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    GRAPH_BACKEND = os.environ.get('GRAPH_BACKEND', 'local').strip().lower()
    
    # 文件上传配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.environ.get('MIROFISH_DATA_DIR', os.path.join(os.path.dirname(__file__), '../uploads'))
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    LOCAL_GRAPH_DB_PATH = os.environ.get('LOCAL_GRAPH_DB_PATH', os.path.join(UPLOAD_FOLDER, 'memory.sqlite3'))
    JOBS_DB_PATH = os.environ.get('JOBS_DB_PATH', os.path.join(UPLOAD_FOLDER, 'jobs.sqlite3'))
    JOB_WORKERS = int(os.environ.get('JOB_WORKERS', '2'))
    JOB_LEASE_SECONDS = int(os.environ.get('JOB_LEASE_SECONDS', '60'))
    # Server entrypoints start the dispatcher explicitly. Importing an app for
    # tests or an administrative script must never launch persisted paid work.
    JOBS_AUTOSTART = os.environ.get('JOBS_AUTOSTART', 'false').lower() == 'true'
    BUDGET_DB_PATH = os.environ.get('BUDGET_DB_PATH', os.path.join(UPLOAD_FOLDER, 'budgets.sqlite3'))
    BUDGET_MAX_CALLS = int(os.environ.get('BUDGET_MAX_CALLS', '1000'))
    BUDGET_MAX_TOKENS = int(os.environ.get('BUDGET_MAX_TOKENS', '1000000'))
    BUDGET_MAX_OUTPUT_TOKENS = int(os.environ.get('BUDGET_MAX_OUTPUT_TOKENS', '250000'))
    BUDGET_MAX_WALL_SECONDS = int(os.environ.get('BUDGET_MAX_WALL_SECONDS', '3600'))
    BUDGET_MAX_COST_USD = float(os.environ['BUDGET_MAX_COST_USD']) if os.environ.get('BUDGET_MAX_COST_USD') else None
    BUDGET_MODEL_PRICING_JSON = os.environ.get('BUDGET_MODEL_PRICING_JSON', '{}')
    
    # 文本处理配置
    DEFAULT_CHUNK_SIZE = 500  # 默认切块大小
    DEFAULT_CHUNK_OVERLAP = 50  # 默认重叠大小
    
    # OASIS模拟配置
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(UPLOAD_FOLDER, 'simulations')
    _SIMULATION_ENV_PYTHON = os.path.abspath(os.path.join(
        os.path.dirname(__file__), '..', '.venv-simulation',
        'Scripts/python.exe' if os.name == 'nt' else 'bin/python',
    ))
    SIMULATION_PYTHON = os.environ.get('SIMULATION_PYTHON') or (
        _SIMULATION_ENV_PYTHON if os.path.isfile(_SIMULATION_ENV_PYTHON) else sys.executable
    )
    SIMULATION_MAX_WALL_SECONDS = int(os.environ.get('SIMULATION_MAX_WALL_SECONDS', '3600'))
    SIMULATION_MAX_CPU_SECONDS = int(os.environ.get('SIMULATION_MAX_CPU_SECONDS', '1800'))
    SIMULATION_MAX_MEMORY_MB = int(os.environ.get('SIMULATION_MAX_MEMORY_MB', '8192'))
    
    # OASIS平台可用动作配置
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent配置
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    @classmethod
    def validate(cls) -> list[str]:
        """验证必要配置"""
        errors: list[str] = []
        from .utils.llm_provider import resolve_llm_settings
        values = {key: getattr(cls, key) for key in dir(cls) if key.startswith(('LLM_', 'OLLAMA_'))}
        try:
            resolve_llm_settings(values)
            resolve_llm_settings(values, use_boost=True)
        except ValueError as exc:
            errors.append(str(exc))
        if not cls.MIROFISH_ACCESS_KEY or len(cls.MIROFISH_ACCESS_KEY) < 32:
            errors.append('MIROFISH_ACCESS_KEY must contain at least 32 characters; run python3 scripts/setup_config.py')
        if cls.GRAPH_BACKEND not in {'local', 'zep'}:
            errors.append('GRAPH_BACKEND must be local or zep')
        if cls.GRAPH_BACKEND == 'zep' and not cls.ZEP_API_KEY:
            errors.append("ZEP_API_KEY 未配置")
        if cls.GRAPH_BACKEND == 'zep' and os.environ.get("ZEP_API_URL"):
            errors.append("ZEP_API_URL 不受支持；MiroFish 仅连接 Zep Cloud")
        if cls.DEBUG:
            import warnings
            warnings.warn("Flask DEBUG mode is enabled. Do not use in production.", RuntimeWarning)
        return errors
