"""
base.py — Base class untuk semua scanning layers.
Setiap layer WAJIB implement method `run()`.
"""

import time
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from config import DiagnosticConfig, DEFAULT_CONFIG
from core.browser import BrowserCaptureResult
from core.session import SessionManager

logger = logging.getLogger(__name__)


@dataclass
class LayerFinding:
    """Satu temuan dari layer"""
    category: str           # 'media', 'api', 'streaming', 'config', 'info'
    subcategory: str        # 'image', 'video', 'hls', 'endpoint', etc
    url: str = ""
    data: Dict = field(default_factory=dict)
    confidence: float = 0.8  # 0.0 - 1.0
    source: str = ""         # method/line yang menemukan
    context: str = ""        # potongan text di sekitar temuan
    

@dataclass
class LayerResult:
    """Hasil dari satu layer"""
    layer_name: str
    success: bool = True
    duration: float = 0.0
    findings: List[LayerFinding] = field(default_factory=list)
    summary: Dict = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    raw_data: Dict = field(default_factory=dict)  # data mentah untuk layer lain
    
    @property
    def finding_count(self) -> int:
        return len(self.findings)
    
    def add_finding(self, **kwargs):
        """Shortcut untuk tambah finding"""
        self.findings.append(LayerFinding(**kwargs))
    
    def get_findings_by_category(self, category: str) -> List[LayerFinding]:
        return [f for f in self.findings if f.category == category]
    
    def get_media_urls(self) -> List[str]:
        return [
            f.url for f in self.findings 
            if f.category == 'media' and f.url
        ]
    
    def get_api_urls(self) -> List[str]:
        return [
            f.url for f in self.findings 
            if f.category == 'api' and f.url
        ]


class BaseLayer(ABC):
    """
    Abstract base class untuk scanning layers.
    
    Setiap layer menerima:
    - url: target URL
    - recon: hasil reconnaissance (dari Phase 1)
    - capture: hasil browser capture (dari Phase 2)
    - session: HTTP session manager
    
    Dan mengembalikan LayerResult.
    """
    
    LAYER_NAME = "base"
    LAYER_DESCRIPTION = "Base Layer"
    
    def __init__(self, config: DiagnosticConfig = None):
        self.config = config or DEFAULT_CONFIG
        self._result = LayerResult(layer_name=self.LAYER_NAME)
    
    async def run(
        self,
        url: str,
        recon,              # ReconResult
        capture: BrowserCaptureResult,
        session: SessionManager
    ) -> LayerResult:
        """
        Main entry point untuk layer.
        Wrap execute() dengan timing & error handling.
        """
        self._result = LayerResult(layer_name=self.LAYER_NAME)
        start = time.time()
        
        try:
            logger.info(f"    🔍 [{self.LAYER_NAME}] {self.LAYER_DESCRIPTION}")
            await self.execute(url, recon, capture, session)
            self._result.success = True
            
        except Exception as e:
            error_msg = f"[{self.LAYER_NAME}] {str(e)}"
            logger.error(f"    ❌ {error_msg}")
            self._result.errors.append(error_msg)
            self._result.success = False
            
        finally:
            self._result.duration = time.time() - start
            self._result.summary = self.summarize()
            logger.info(
                f"    ✅ [{self.LAYER_NAME}] "
                f"{self._result.finding_count} findings "
                f"in {self._result.duration:.2f}s"
            )
        
        return self._result
    
    @abstractmethod
    async def execute(
        self,
        url: str,
        recon,
        capture: BrowserCaptureResult,
        session: SessionManager
    ):
        """
        Override ini di setiap layer.
        Tambahkan findings ke self._result
        """
        pass
    
    def summarize(self) -> Dict:
        """Generate summary dari findings. Override jika perlu."""
        categories = {}
        for f in self._result.findings:
            key = f"{f.category}:{f.subcategory}"
            categories[key] = categories.get(key, 0) + 1
        
        return {
            'total_findings': self._result.finding_count,
            'categories': categories,
            'errors': len(self._result.errors),
        }
    
    def add_finding(self, **kwargs):
        """Shortcut"""
        self._result.add_finding(**kwargs)
    
    def add_error(self, msg: str):
        """Shortcut"""
        self._result.errors.append(f"[{self.LAYER_NAME}] {msg}")