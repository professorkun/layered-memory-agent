"""layered-memory：一套可单独抽出来接入自己机器人的分层记忆系统。

公开 API（稳定，其他模块视为内部实现，可替换）::

    from layered_memory import MemorySystem, Config, Layer, Stage, MemoryItem

分层：上下文 / 事实层 / 事件层 / 生长层（底感-感觉-沉淀）/ 意识层 / 知识库。
零第三方依赖，Python 3.10+ 标准库即可运行。
"""
from .config import Config, DEFAULTS
from .models import Layer, Stage, MemoryItem, Fact, Feeling, Sediment, Edge
from .decay import compute, calibrate
from .scoring import cue_match, noise_level, heuristic
from .retrieval import recall
from .store import Store
from .layers import LayeredView
from .affect import AffinityLedger, EmotionState, AffectBridge
from .pipeline import MemorySystem

__version__ = "1.0.0"
__all__ = [
    "MemorySystem", "Config", "DEFAULTS", "Layer", "Stage", "MemoryItem",
    "Fact", "Feeling", "Sediment", "Edge", "compute", "calibrate",
    "cue_match", "noise_level", "heuristic", "recall", "Store",
    "LayeredView", "AffinityLedger", "EmotionState", "AffectBridge", "__version__",
]
