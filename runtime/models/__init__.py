# Models package
from .data_models import EPCReadEvent, SensorState, SensorDirection, ReaderSettings, RXInventoryTag
from .reader_model import ReaderModel, AnalysisResult

# Optional: AI model support (graceful if deps missing)
try:
    from .tag_analyzer import TagAnalyzer
except ImportError:
    TagAnalyzer = None

__all__ = [
    'EPCReadEvent',
    'SensorState',
    'SensorDirection',
    'ReaderSettings',
    'RXInventoryTag',
    'ReaderModel',
    'AnalysisResult',
    'TagAnalyzer',
]
